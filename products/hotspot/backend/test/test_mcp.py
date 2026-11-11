from __future__ import annotations

import io
import os
import sys
import json
import time
import socket
import asyncio
import threading
import contextlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from django.test import RequestFactory

import httpx
import uvicorn
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from products.hotspot.backend import views
from products.hotspot.backend.mcp import handle_mcp_http_payload, run_stdio_server
from products.hotspot.backend.service import HotspotService, reset_service
from products.hotspot.backend.test.helpers import make_service
from products.hotspot.standalone import server as standalone_server


@pytest.fixture
def mcp_service() -> Iterator[HotspotService]:
    service = make_service(
        HOTSPOT_SITE_URL="https://hotspot.example.com",
        HOTSPOT_REPLAY_ENABLED="true",
        HOTSPOT_DEMO_ENABLED="false",
    )
    reset_service(service)
    yield service
    reset_service(None)


def _rpc(method: str, params: dict[str, Any] | None = None, msg_id: int | str = 1) -> bytes:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        payload["params"] = params
    return json.dumps(payload).encode("utf-8")


def _call_tool(
    service: HotspotService,
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    headers: dict[str, str] | None = None,
    query_params: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    status, resp = handle_mcp_http_payload(
        _rpc("tools/call", {"name": name, "arguments": arguments or {}}, msg_id=42),
        service=service,
        headers=headers,
        query_params=query_params,
    )
    assert isinstance(resp, dict)
    return status, resp


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_mcp_initialize_discovery_resources_and_prompts(mcp_service: HotspotService) -> None:
    status, init_resp = handle_mcp_http_payload(
        _rpc(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0.0"},
            },
        ),
        service=mcp_service,
    )
    assert status == 200
    result = init_resp["result"]
    assert result["protocolVersion"] == "2025-11-25"
    assert result["serverInfo"]["name"] == "hotspot"
    assert "hs_live_" in result["instructions"]
    assert "hs_sec_" in result["instructions"]
    assert set(result["capabilities"]) >= {"tools", "resources", "prompts"}

    notif_status, notif_resp = handle_mcp_http_payload(
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode("utf-8"),
        service=mcp_service,
    )
    assert (notif_status, notif_resp) == (202, None)

    _, tools_resp = handle_mcp_http_payload(_rpc("tools/list"), service=mcp_service)
    tool_names = {t["name"] for t in tools_resp["result"]["tools"]}
    assert {
        "get_important_instructions",
        "register_site",
        "get_site_setup",
        "verify_connection",
        "send_test_session",
        "get_overview",
        "get_chart_data",
        "get_funnel_analysis",
        "get_user_flows",
        "get_breakage_and_errors",
        "get_pages_and_vitals",
        "get_referrers_and_ai_traffic",
        "get_realtime_snapshot",
        "list_sessions",
        "get_session_detail",
        "get_live_events",
        "delete_site",
    }.issubset(tool_names)

    _, res_resp = handle_mcp_http_payload(_rpc("resources/list"), service=mcp_service)
    uris = {r["uri"] for r in res_resp["result"]["resources"]}
    assert {
        "hotspot://instructions/important",
        "hotspot://instructions/sdk-api",
        "hotspot://instructions/analytics-and-charts",
        "hotspot://site/current",
    } == uris

    _, tpl_resp = handle_mcp_http_payload(_rpc("resources/templates/list"), service=mcp_service)
    assert tpl_resp["result"]["resourceTemplates"][0]["uriTemplate"] == "hotspot://instructions/{topic}"

    _, read_resp = handle_mcp_http_payload(
        _rpc("resources/read", {"uri": "hotspot://instructions/sdk-api"}),
        service=mcp_service,
    )
    sdk_doc = read_resp["result"]["contents"][0]["text"]
    assert "window.hotspot.capture(" in sdk_doc
    assert "data-hotspot-mask" in sdk_doc

    _, unauth_site_res = handle_mcp_http_payload(
        _rpc("resources/read", {"uri": "hotspot://site/current"}),
        service=mcp_service,
    )
    assert unauth_site_res["error"]["code"] == -32001

    _, prompts_resp = handle_mcp_http_payload(_rpc("prompts/list"), service=mcp_service)
    prompt_names = {p["name"] for p in prompts_resp["result"]["prompts"]}
    assert prompt_names == {
        "install-hotspot",
        "test-and-verify-session",
        "visualize-site-analytics",
        "debug-dropoffs-and-ux",
    }

    _, prompt_get = handle_mcp_http_payload(
        _rpc("prompts/get", {"name": "install-hotspot", "arguments": {"framework": "nextjs_app"}}),
        service=mcp_service,
    )
    assert "nextjs_app" in prompt_get["result"]["messages"][0]["content"]["text"]


def test_mcp_strict_fail_closed_auth_and_tenant_isolation(mcp_service: HotspotService) -> None:
    # Create two independent sites via register_site.
    _, reg_a = _call_tool(
        mcp_service, "register_site", {"name": "Alpha", "domain": "alpha.example.com", "framework": "nextjs_app"}
    )
    assert not reg_a["result"]["isError"]
    site_a = reg_a["result"]["structuredContent"]["site"]
    assert site_a["api_key"].startswith("hs_live_")
    assert site_a["secret_key"].startswith("hs_sec_")
    assert site_a["api_key"] in reg_a["result"]["structuredContent"]["integration_guide"]["code"]
    assert site_a["secret_key"] not in reg_a["result"]["structuredContent"]["integration_guide"]["code"]

    _, reg_b = _call_tool(mcp_service, "register_site", {"name": "Beta", "domain": "beta.example.com"})
    assert not reg_b["result"]["isError"]
    site_b = reg_b["result"]["structuredContent"]["site"]

    # Re-registering Alpha without confirmation_key MUST fail.
    _, hijacked = _call_tool(mcp_service, "register_site", {"name": "Alpha Hijack", "domain": "alpha.example.com"})
    assert hijacked["result"]["isError"] is True
    assert "already registered" in hijacked["result"]["content"][0]["text"].lower()

    # Re-registering Alpha WITH its secret key rotates cleanly.
    _, rotated = _call_tool(
        mcp_service,
        "register_site",
        {"name": "Alpha", "domain": "alpha.example.com", "confirmation_key": site_a["secret_key"]},
    )
    assert not rotated["result"]["isError"]
    site_a = rotated["result"]["structuredContent"]["site"]

    # Data tools fail closed when unauthenticated.
    for tool_name, extra in (
        ("get_site_setup", {}),
        ("verify_connection", {}),
        ("send_test_session", {}),
        ("get_overview", {}),
        ("get_chart_data", {}),
        ("get_funnel_analysis", {}),
        ("get_user_flows", {}),
        ("get_breakage_and_errors", {}),
        ("get_pages_and_vitals", {}),
        ("get_referrers_and_ai_traffic", {}),
        ("get_realtime_snapshot", {}),
        ("list_sessions", {}),
        ("get_session_detail", {"session_id": "any"}),
        ("get_live_events", {}),
        ("delete_site", {"confirm": True}),
    ):
        _, blocked = _call_tool(mcp_service, tool_name, extra)
        assert blocked["result"]["isError"] is True, f"{tool_name} should fail closed without secret_key"

    # Public hs_live_ key is rejected as a bearer token on HTTP & as secret_key arg.
    status_live, resp_live = handle_mcp_http_payload(
        _rpc("tools/list"),
        service=mcp_service,
        headers={"Authorization": f"Bearer {site_a['api_key']}"},
    )
    assert status_live == 401
    assert "hs_live_" in resp_live["error"]["message"]

    # Unknown bearer token returns 401.
    status_bad, _ = handle_mcp_http_payload(
        _rpc("tools/list"),
        service=mcp_service,
        headers={"Authorization": "Bearer hs_sec_nonexistent_token"},
    )
    assert status_bad == 401

    # Seed one synthetic session on Site B and verify Site A cannot see it.
    _, seeded_b = _call_tool(
        mcp_service,
        "send_test_session",
        {"entry_path": "/secret-beta"},
        headers={"Authorization": f"Bearer {site_b['secret_key']}"},
    )
    assert not seeded_b["result"]["isError"]
    beta_session_id = seeded_b["result"]["structuredContent"]["session_id"]

    # Site A auth header + Site B secret_key tool arg -> mismatch error.
    _, mismatch = _call_tool(
        mcp_service,
        "get_overview",
        {"secret_key": site_b["secret_key"]},
        headers={"Authorization": f"Bearer {site_a['secret_key']}"},
    )
    assert mismatch["result"]["isError"] is True
    assert "mismatch" in mismatch["result"]["content"][0]["text"].lower()

    # Site A header requesting Beta's session -> not accessible.
    _, cross_detail = _call_tool(
        mcp_service,
        "get_session_detail",
        {"session_id": beta_session_id},
        headers={"Authorization": f"Bearer {site_a['secret_key']}"},
    )
    assert cross_detail["result"]["isError"] is True

    # Site A list_sessions / get_live_events / get_overview must see 0 sessions from Site B.
    _, list_a = _call_tool(
        mcp_service,
        "list_sessions",
        {},
        headers={"Authorization": f"Bearer {site_a['secret_key']}"},
    )
    assert list_a["result"]["structuredContent"]["returned"] == 0


def test_mcp_full_suite_testing_charts_and_diagnostics(mcp_service: HotspotService) -> None:
    _, reg = _call_tool(mcp_service, "register_site", {"name": "Storefront", "domain": "shop.example.com"})
    secret_key = reg["result"]["structuredContent"]["site"]["secret_key"]
    auth_headers = {"X-Hotspot-Secret-Key": secret_key}

    # Verify before data exists -> disconnected with troubleshooting hints.
    _, before = _call_tool(mcp_service, "verify_connection", {}, headers=auth_headers)
    before_data = before["result"]["structuredContent"]
    assert before_data["connected"] is False
    assert len(before_data["troubleshooting"]) >= 3

    # Setup guide for Next.js App Router never exposes secret key inside framework snippet.
    _, setup = _call_tool(mcp_service, "get_site_setup", {"framework": "nextjs_app"}, headers=auth_headers)
    setup_data = setup["result"]["structuredContent"]
    assert "next/script" in setup_data["integration_guide"]["code"]
    assert secret_key not in setup_data["integration_guide"]["code"]

    # Send synthetic test session with conversion + error + replay chunk.
    _, test_sess = _call_tool(
        mcp_service,
        "send_test_session",
        {
            "entry_path": "/",
            "secondary_path": "/checkout",
            "include_conversion": True,
            "include_error": True,
            "include_replay": True,
        },
        headers=auth_headers,
    )
    assert not test_sess["result"]["isError"]
    created_data = test_sess["result"]["structuredContent"]
    session_id = created_data["session_id"]
    assert created_data["events_ingested"] >= 6
    assert created_data["recording"]["status"] == 1

    # Verify connection now connected.
    _, after = _call_tool(mcp_service, "verify_connection", {}, headers=auth_headers)
    after_data = after["result"]["structuredContent"]
    assert after_data["connected"] is True
    assert after_data["telemetry_summary"]["has_session_recordings"] is True

    # Pull normalized charts.
    _, charts_resp = _call_tool(mcp_service, "get_chart_data", {"chart_type": "all", "hours": 24}, headers=auth_headers)
    charts = charts_resp["result"]["structuredContent"]["charts"]
    assert len(charts["trend_timeseries"]["points"]) > 0
    assert charts["trend_timeseries"]["totals"]["sessions"] == 1
    assert charts["conversion_funnel"]["steps"][0]["count"] == 1
    assert len(charts["top_pages"]["rows"]) >= 2
    assert charts["traffic_channels"]["ai_sessions"] == 1
    assert charts["device_browser_breakdown"]["browser"][0]["name"] == "Chrome"
    assert len(charts["realtime_activity"]["sessions_per_minute_last_30m"]) == 30

    # Custom funnel + auto suggested funnels.
    _, funnel_resp = _call_tool(
        mcp_service,
        "get_funnel_analysis",
        {"steps": ["path:/", "path:/checkout", "event:$form_submit"], "hours": 24},
        headers=auth_headers,
    )
    funnel = funnel_resp["result"]["structuredContent"]["funnel"]
    assert funnel["conversion_rate"] == 100.0
    assert isinstance(funnel["suggested_funnels"], list)

    # Flows, breakage, pages+vitals, referrers, realtime, broken session list, live events.
    _, flows = _call_tool(mcp_service, "get_user_flows", {}, headers=auth_headers)
    assert flows["result"]["structuredContent"]["paths"]["transitions"][0] == {
        "from": "/",
        "to": "/checkout",
        "count": 1,
        "share": 100.0,
    }

    _, breakage = _call_tool(mcp_service, "get_breakage_and_errors", {}, headers=auth_headers)
    brk_data = breakage["result"]["structuredContent"]
    assert len(brk_data["friction"]["rage_clicks"]) == 1
    assert len(brk_data["errors"]) == 1
    assert len(brk_data["broken_sessions"]) == 1

    _, pages = _call_tool(mcp_service, "get_pages_and_vitals", {}, headers=auth_headers)
    assert pages["result"]["structuredContent"]["site_web_vitals"]["lcp_ms"] == 1180

    _, refs = _call_tool(mcp_service, "get_referrers_and_ai_traffic", {}, headers=auth_headers)
    assert refs["result"]["structuredContent"]["referrers"]["ai_sessions"] == 1

    _, rt = _call_tool(mcp_service, "get_realtime_snapshot", {}, headers=auth_headers)
    assert rt["result"]["structuredContent"]["realtime"]["active_now"] >= 1

    _, broken_list = _call_tool(mcp_service, "list_sessions", {"status": "broken"}, headers=auth_headers)
    assert broken_list["result"]["structuredContent"]["returned"] == 1

    # Session detail with extracted replay console + network diagnostics.
    _, detail_resp = _call_tool(
        mcp_service,
        "get_session_detail",
        {"session_id": session_id, "include_recording_diagnostics": True},
        headers=auth_headers,
    )
    detail = detail_resp["result"]["structuredContent"]
    assert detail["event_count"] >= 6
    diag = detail["recording_diagnostics"]
    assert diag["present"] is True
    assert diag["console_errors_or_warnings_count"] == 1
    assert diag["failed_network_requests_count"] == 1
    assert diag["network_requests"][0]["status"] == 500

    # Live events filtered by event_name.
    _, feed = _call_tool(mcp_service, "get_live_events", {"event_name": "$exception"}, headers=auth_headers)
    assert feed["result"]["structuredContent"]["count"] == 1

    # Delete requires confirm=True.
    _, del_fail = _call_tool(mcp_service, "delete_site", {"confirm": False}, headers=auth_headers)
    assert del_fail["result"]["isError"] is True
    _, del_ok = _call_tool(mcp_service, "delete_site", {"confirm": True}, headers=auth_headers)
    assert del_ok["result"]["structuredContent"]["deleted"] is True


def test_django_mcp_endpoint_parity(mcp_service: HotspotService) -> None:
    factory = RequestFactory()
    pre = views.mcp_endpoint(factory.options("/mcp"))
    assert (pre.status_code, pre["Access-Control-Allow-Origin"]) == (204, "*")

    disc = views.mcp_endpoint(factory.get("/mcp", HTTP_ACCEPT="application/json"))
    assert disc.status_code == 200
    assert "streamable_http" in json.loads(disc.content)["transports"]

    sse_probe = views.mcp_endpoint(factory.get("/mcp", HTTP_ACCEPT="text/event-stream"))
    assert sse_probe.status_code == 405

    unauth = views.mcp_endpoint(
        factory.post(
            "/mcp",
            data=_rpc("tools/list"),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer wrong-key",
        )
    )
    assert unauth.status_code == 401

    batch = views.mcp_endpoint(
        factory.post(
            "/mcp",
            data=json.dumps(
                [
                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                    {"jsonrpc": "2.0", "id": 9, "method": "ping"},
                ]
            ).encode("utf-8"),
            content_type="application/json",
        )
    )
    assert batch.status_code == 200
    assert json.loads(batch.content) == [{"jsonrpc": "2.0", "id": 9, "result": {}}]


def test_mcp_stdio_loop_and_cli(mcp_service: HotspotService) -> None:
    stdin = io.StringIO(
        "\n".join(
            [
                _rpc(
                    "initialize",
                    {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "stdio", "version": "1"},
                    },
                    msg_id=1,
                ).decode(),
                _rpc(
                    "tools/call", {"name": "get_important_instructions", "arguments": {"topic": "security"}}, msg_id=2
                ).decode(),
            ]
        )
        + "\n"
    )
    stdout = io.StringIO()
    run_stdio_server(service=mcp_service, stdin=stdin, stdout=stdout)
    lines = [json.loads(line) for line in stdout.getvalue().strip().splitlines()]
    assert len(lines) == 2
    assert lines[0]["result"]["serverInfo"]["name"] == "hotspot"
    assert "hs_sec_" in lines[1]["result"]["structuredContent"]["instructions"]


def test_official_mcp_sdk_clients_streamable_http_and_sse(mcp_service: HotspotService) -> None:
    _, reg = _call_tool(mcp_service, "register_site", {"name": "Live Wire", "domain": "wire.example.com"})
    secret_key = reg["result"]["structuredContent"]["site"]["secret_key"]

    port = _free_port()
    config = uvicorn.Config(standalone_server.app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            try:
                res = httpx.get(f"{base_url}/mcp", timeout=0.5)
                if res.status_code == 200:
                    break
            except Exception:
                time.sleep(0.05)
        else:
            raise AssertionError("Uvicorn test server failed to start")

        async def _verify_both_transports() -> None:
            headers = {"Authorization": f"Bearer {secret_key}"}
            # 1. Streamable HTTP transport (/mcp)
            async with streamablehttp_client(f"{base_url}/mcp", headers=headers) as (read_s, write_s, _):
                async with ClientSession(read_s, write_s) as session:
                    init_res = await session.initialize()
                    assert init_res.serverInfo.name == "hotspot"

                    tools = await session.list_tools()
                    assert len(tools.tools) == 17

                    resources = await session.list_resources()
                    assert any(str(r.uri) == "hotspot://instructions/important" for r in resources.resources)
                    res_read = await session.read_resource("hotspot://instructions/important")
                    assert "window.hotspot.capture" in res_read.contents[0].text

                    prompts = await session.list_prompts()
                    assert len(prompts.prompts) == 4
                    prompt_res = await session.get_prompt("visualize-site-analytics", {"hours": "24"})
                    assert "get_chart_data" in prompt_res.messages[0].content.text

                    seeded = await session.call_tool("send_test_session", {"include_error": True})
                    assert not seeded.isError
                    created_sid = seeded.structuredContent["session_id"]

                    chart_res = await session.call_tool("get_chart_data", {"chart_type": "trend_timeseries"})
                    assert not chart_res.isError
                    assert chart_res.structuredContent["charts"]["trend_timeseries"]["totals"]["sessions"] >= 1

                    det = await session.call_tool("get_session_detail", {"session_id": created_sid})
                    assert not det.isError
                    assert det.structuredContent["recording_diagnostics"]["console_errors_or_warnings_count"] == 1

            # 2. Legacy SSE transport (/mcp/sse)
            async with sse_client(f"{base_url}/mcp/sse", headers=headers) as (sse_read, sse_write):
                async with ClientSession(sse_read, sse_write) as sse_session:
                    sse_init = await sse_session.initialize()
                    assert sse_init.serverInfo.name == "hotspot"
                    verified = await sse_session.call_tool("verify_connection", {})
                    assert not verified.isError
                    assert verified.structuredContent["connected"] is True

        asyncio.run(_verify_both_transports())
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)
        with contextlib.suppress(Exception):
            standalone_server._MCP_SSE_QUEUES.clear()


def test_official_mcp_sdk_stdio_subprocess() -> None:
    repo_root = str(Path(__file__).resolve().parents[4])

    async def _run_stdio() -> None:
        env = {**os.environ, "PYTHONPATH": repo_root, "HOTSPOT_DEMO_ENABLED": "false"}
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "products.hotspot.mcp"],
            env=env,
            cwd=repo_root,
        )
        async with stdio_client(params) as (read_s, write_s):
            async with ClientSession(read_s, write_s) as session:
                init_res = await session.initialize()
                assert init_res.serverInfo.name == "hotspot"

                reg = await session.call_tool("register_site", {"name": "Stdio Site", "domain": "stdio.example.com"})
                assert not reg.isError
                sec = reg.structuredContent["site"]["secret_key"]

                seeded = await session.call_tool("send_test_session", {"secret_key": sec})
                assert not seeded.isError

                ov = await session.call_tool("get_overview", {"secret_key": sec})
                assert not ov.isError
                assert ov.structuredContent["metrics"]["sessions"] == 1

    asyncio.run(_run_stdio())


def test_mcp_multi_site_shared_secret_key_isolation(mcp_service: HotspotService) -> None:
    _, reg_other = _call_tool(mcp_service, "register_site", {"name": "Other Co", "domain": "other.mcp.example.com"})
    assert not reg_other["result"]["isError"]
    _call_tool(
        mcp_service,
        "send_test_session",
        {"secret_key": reg_other["result"]["structuredContent"]["site"]["secret_key"]},
    )

    _, unauth_same = _call_tool(
        mcp_service,
        "register_site",
        {"name": "Unauth", "domain": "unauth.mcp.example.com", "key_mode": "same"},
    )
    assert unauth_same["result"]["isError"] is True

    _, reg_a = _call_tool(mcp_service, "register_site", {"name": "Alpha MCP", "domain": "alpha.mcp.example.com"})
    sec_shared = reg_a["result"]["structuredContent"]["site"]["secret_key"]
    headers = {"Authorization": f"Bearer {sec_shared}"}

    _, reg_b = _call_tool(
        mcp_service,
        "register_site",
        {"name": "Beta MCP", "domain": "beta.mcp.example.com", "key_mode": "same"},
        headers=headers,
    )
    assert not reg_b["result"]["isError"]
    assert reg_b["result"]["structuredContent"]["site"]["secret_key"] == sec_shared

    _call_tool(mcp_service, "send_test_session", {"site": "alpha.mcp.example.com"}, headers=headers)
    _call_tool(mcp_service, "send_test_session", {"site": "beta.mcp.example.com"}, headers=headers)

    _, ov_all = _call_tool(mcp_service, "get_overview", {}, headers=headers)
    assert not ov_all["result"]["isError"]
    all_content = ov_all["result"]["structuredContent"]
    assert all_content["metrics"]["sessions"] == 2
    assert all_content["scope"]["sessions_stored"] == 2

    _, ov_b = _call_tool(mcp_service, "get_overview", {"site": "beta.mcp.example.com"}, headers=headers)
    assert not ov_b["result"]["isError"]
    assert ov_b["result"]["structuredContent"]["metrics"]["sessions"] == 1

    _, setup_b = _call_tool(mcp_service, "get_site_setup", {"site": "beta.mcp.example.com"}, headers=headers)
    assert not setup_b["result"]["isError"]
    assert setup_b["result"]["structuredContent"]["site"]["domain"] == "beta.mcp.example.com"
    assert len(setup_b["result"]["structuredContent"]["sites_sharing_secret_key"]) == 2

    _, forbidden = _call_tool(mcp_service, "get_overview", {"site": "other.mcp.example.com"}, headers=headers)
    assert forbidden["result"]["isError"] is True
