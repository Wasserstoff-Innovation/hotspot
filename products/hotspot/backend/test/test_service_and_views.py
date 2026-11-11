from __future__ import annotations

import gzip
import json
import stat
import time
from collections.abc import Iterator
from typing import Any

import pytest

from django.test import Client, RequestFactory

from starlette.testclient import TestClient

from products.hotspot.backend import views
from products.hotspot.backend.auth import DASHBOARD_COOKIE, make_dashboard_token, verify_dashboard_token
from products.hotspot.backend.service import HotspotService, reset_service
from products.hotspot.backend.templating import render_string
from products.hotspot.backend.test.helpers import batch_body, event_item, make_service
from products.hotspot.mcp import save_credentials_bundle
from products.hotspot.standalone import server as standalone_server

NOW = 1_700_000_000.0


@pytest.fixture
def protected_service() -> Iterator[HotspotService]:
    service = make_service(HOTSPOT_DASHBOARD_PASSWORD="open-sesame", HOTSPOT_API_KEYS="hs_test")
    reset_service(service)
    yield service
    reset_service(None)


def test_dashboard_tokens_expire_and_reject_tampering() -> None:
    token = make_dashboard_token("secret", NOW, ttl=60)
    assert verify_dashboard_token("secret", token, NOW + 30)
    assert not verify_dashboard_token("secret", token, NOW + 61)
    assert not verify_dashboard_token("other-secret", token, NOW)
    expires, _, signature = token.partition(".")
    assert not verify_dashboard_token("secret", f"{int(expires) + 9999}.{signature}", NOW)


def test_authorization_is_open_only_without_a_password() -> None:
    assert make_service().is_authorized(None, None)
    service = make_service(HOTSPOT_DASHBOARD_PASSWORD="pw")
    assert not service.is_authorized(None, None)
    assert service.login("wrong") is None
    assert service.is_authorized(service.login("pw", now=NOW), None, now=NOW + 1)
    assert service.is_authorized(None, "Bearer pw")
    assert not service.is_authorized(None, "Bearer nope")


def test_placeholder_renderer_escapes_values() -> None:
    rendered = render_string(
        "<a href='{{ site_url }}'>{{page_title}}</a>{{ missing }}", {"site_url": 'x"><s>', "page_title": "A & B"}
    )
    assert rendered == "<a href='x&quot;&gt;&lt;s&gt;'>A &amp; B</a>"


def test_dashboard_and_api_require_sign_in_when_protected(protected_service: HotspotService) -> None:
    factory = RequestFactory()
    assert views.dashboard(factory.get("/dashboard/")).status_code == 302
    assert views.overview(factory.get("/hotspot/api/overview/")).status_code == 401

    token = protected_service.login("open-sesame")
    assert token
    signed_in = factory.get("/dashboard/")
    signed_in.COOKIES[DASHBOARD_COOKIE] = token
    page = views.dashboard(signed_in)
    assert page.status_code == 200
    assert "'nonce-" in page["Content-Security-Policy"] and page["Cache-Control"] == "no-store"

    bearer = factory.get("/hotspot/api/overview/", HTTP_AUTHORIZATION="Bearer open-sesame")
    assert json.loads(views.overview(bearer).content)["status"] == 1


def test_capture_endpoint_records_events_and_answers_cors(protected_service: HotspotService) -> None:
    factory = RequestFactory()
    preflight = views.capture(factory.options("/hotspot/batch/"))
    assert (preflight.status_code, preflight["Access-Control-Allow-Origin"]) == (204, "*")

    body = batch_body([event_item("$pageview", "sess-1", NOW, **{"$pathname": "/docs"})], api_key="hs_test")
    accepted = views.capture(factory.post("/hotspot/batch/", data=body, content_type="text/plain"))
    assert json.loads(accepted.content) == {"status": 1, "received": 1}
    assert [session["entry_page"] for session in protected_service.store.sessions()] == ["/docs"]

    refused = views.capture(
        factory.post("/hotspot/batch/", data=batch_body([], api_key="other"), content_type="text/plain")
    )
    assert refused.status_code == 401


def test_recording_endpoint_splices_stored_chunks_into_one_json_document(protected_service: HotspotService) -> None:
    factory = RequestFactory()
    # The view reads the real clock, and a recording older than the ingestion window is refused.
    start = int(time.time()) * 1000
    meta = {"type": 4, "timestamp": start, "data": {"href": "https://example.com/", "width": 1280, "height": 800}}
    snapshot = {"type": 2, "timestamp": start + 1, "data": {"node": {"type": 0, "childNodes": [], "id": 1}}}
    mutation = {"type": 3, "timestamp": start + 500, "data": {"source": 1, "positions": [{"x": 1, "y": 2, "id": 1}]}}

    def post(events: list[dict[str, Any]], compressed: bool = False) -> dict[str, Any]:
        raw = json.dumps({"api_key": "hs_test", "session_id": "sess-1", "events": events}).encode()
        path = "/hotspot/replay/?compression=gzip" if compressed else "/hotspot/replay/"
        request = factory.post(path, data=gzip.compress(raw) if compressed else raw, content_type="text/plain")
        return json.loads(views.capture_recording(request).content)

    assert post([meta, snapshot], compressed=True) == {"status": 1, "received": 2, "stored": True}
    assert post([mutation])["received"] == 1

    auth = {"Authorization": "Bearer open-sesame"}
    assert views.session_recording(factory.get("/r"), "sess-1").status_code == 401
    assert views.session_recording(factory.get("/r", headers=auth), "missing").status_code == 404
    whole = json.loads(views.session_recording(factory.get("/r", headers=auth), "sess-1").content)
    assert whole["events"] == [meta, snapshot, mutation]
    assert (whole["from"], whole["next"], whole["truncated"], whole["started_at"]) == (0, 2, False, start / 1000)
    tail = json.loads(views.session_recording(factory.get("/r?from=1", headers=auth), "sess-1").content)
    assert tail["events"] == [mutation] and tail["next"] == 2
    detail = json.loads(views.session_detail(factory.get("/d", headers=auth), "sess-1").content)
    assert detail["session"]["has_recording"] is True and detail["session"]["recording_events"] == 3

    recorder = views.asset(factory.get("/hotspot/assets/recorder.js"), "recorder.js")
    assert (recorder.status_code, recorder["Access-Control-Allow-Origin"]) == (200, "*")
    assert recorder["Content-Type"].startswith("application/javascript")
    world = json.loads(views.asset(factory.get("/hotspot/assets/world.json"), "world.json").content)
    assert "IN" in world["countries"] and world["centers"]["IN"]
    assert views.asset(factory.get("/hotspot/assets/other.js"), "other.js").status_code == 404


def test_sites_management_and_scoping(protected_service: HotspotService) -> None:
    factory = RequestFactory()
    auth = {"Authorization": "Bearer open-sesame"}

    # 1. List default sites
    res = views.sites_endpoint(factory.get("/hotspot/api/sites/", headers=auth))
    data = json.loads(res.content)
    assert data["status"] == 1
    assert any(s["domain"] == "buxa.ai" for s in data["sites"])

    # 2. Create new site
    post_res = views.sites_endpoint(
        factory.post(
            "/hotspot/api/sites/",
            data=json.dumps({"name": "Client App", "domain": "client.example.com"}),
            content_type="application/json",
            headers=auth,
        )
    )
    new_site = json.loads(post_res.content)["site"]
    assert new_site["domain"] == "client.example.com"
    new_key = new_site["api_key"]
    assert new_key.startswith("hs_live_")

    # 3. Newly created site API key is dynamically accepted by capture
    now = time.time()
    body = batch_body(
        [
            event_item(
                "$pageview",
                "s1",
                now,
                "v1",
                **{
                    "$current_url": "https://client.example.com/pricing",
                    "$host": "client.example.com",
                    "$pathname": "/pricing",
                },
            )
        ],
        api_key=new_key,
    )
    capture_res = views.capture(factory.post("/hotspot/batch/", data=body, content_type="text/plain"))
    assert capture_res.status_code == 200

    # 4. Overview filtering by site
    # Filter for client.example.com: matches
    overview_filtered = json.loads(
        views.overview(factory.get("/hotspot/api/overview/?site=client.example.com", headers=auth)).content
    )
    assert overview_filtered["metrics"]["sessions"] == 1
    assert overview_filtered["sessions"][0]["session_id"] == "s1"

    # Filter for non-matching site: returns 0
    overview_other = json.loads(
        views.overview(factory.get("/hotspot/api/overview/?site=other.com", headers=auth)).content
    )
    assert overview_other["metrics"]["sessions"] == 0


def test_domain_secret_key_auth_and_collision_protection(protected_service: HotspotService) -> None:
    factory = RequestFactory()

    # 1. Public user registers a domain without prior authentication
    reg_res = views.sites_endpoint(
        factory.post(
            "/hotspot/api/sites/",
            data=json.dumps({"name": "Acme SaaS", "domain": "acme.com"}),
            content_type="application/json",
        )
    )
    assert reg_res.status_code == 200
    site_data = json.loads(reg_res.content)["site"]
    assert site_data["domain"] == "acme.com"
    secret_key = site_data["secret_key"]
    api_key = site_data["api_key"]
    assert secret_key.startswith("hs_sec_")
    assert api_key.startswith("hs_live_")
    assert DASHBOARD_COOKIE in reg_res.cookies

    # 2. Collision protection: Second user attempts to register the same domain
    hijack_res = views.sites_endpoint(
        factory.post(
            "/hotspot/api/sites/",
            data=json.dumps({"name": "Attacker", "domain": "https://acme.com"}),
            content_type="application/json",
        )
    )
    assert hijack_res.status_code == 409
    err = json.loads(hijack_res.content)["error"]
    assert "already registered" in err

    # 3. Collision protection: Attacker provides wrong confirmation key
    fail_confirm = views.sites_endpoint(
        factory.post(
            "/hotspot/api/sites/",
            data=json.dumps({"name": "Acme SaaS", "domain": "acme.com", "confirmation_key": "wrong-key"}),
            content_type="application/json",
        )
    )
    assert fail_confirm.status_code == 409
    assert "invalid" in json.loads(fail_confirm.content)["error"]

    # 4. Legitimate owner provides correct secret key -> credential rotation succeeds
    rotate_res = views.sites_endpoint(
        factory.post(
            "/hotspot/api/sites/",
            data=json.dumps({"name": "Acme SaaS Rotated", "domain": "acme.com", "confirmation_key": secret_key}),
            content_type="application/json",
        )
    )
    assert rotate_res.status_code == 200
    rotated_site = json.loads(rotate_res.content)["site"]
    new_secret_key = rotated_site["secret_key"]
    assert new_secret_key != secret_key

    # 5. User signs in with domain + secret key
    login_req = factory.post(
        "/dashboard/login/",
        data=json.dumps({"domain": "acme.com", "secret_key": new_secret_key}),
        content_type="application/json",
    )
    login_res = views.login(login_req)
    assert login_res.status_code == 200
    login_data = json.loads(login_res.content)
    assert login_data["status"] == 1
    assert login_data["scope"] == "acme.com"
    token = login_res.cookies[DASHBOARD_COOKIE].value

    # 6. Tenant isolation: Scoped user overview only shows acme.com
    scoped_overview_req = factory.get("/hotspot/api/overview/")
    scoped_overview_req.COOKIES[DASHBOARD_COOKIE] = token
    overview_res = views.overview(scoped_overview_req)
    overview_data = json.loads(overview_res.content)
    assert overview_data["status"] == 1
    assert overview_data["scope"]["is_admin"] is False
    assert overview_data["scope"]["authorized_site"] == "acme.com"
    assert len(overview_data["sites"]) == 1
    assert overview_data["sites"][0]["domain"] == "acme.com"


def test_add_more_sites_without_logout_same_or_new_secret_key() -> None:
    from starlette.testclient import TestClient  # noqa: PLC0415

    from products.hotspot.standalone import server as standalone_server  # noqa: PLC0415

    service = make_service(
        HOTSPOT_DASHBOARD_PASSWORD="master-secret-password",
        HOTSPOT_DEMO_ENABLED="false",
    )
    reset_service(service)
    factory = RequestFactory()
    try:
        # 1. Create an unrelated third-party site first
        unauth_other = views.sites_endpoint(
            factory.post(
                "/hotspot/api/sites/",
                data=json.dumps({"name": "Unrelated", "domain": "unrelated.example.com"}),
                content_type="application/json",
            )
        )
        assert unauth_other.status_code == 200
        other_site = json.loads(unauth_other.content)["site"]

        # Unauthenticated key_mode='same' MUST be rejected cleanly
        unauth_same = views.sites_endpoint(
            factory.post(
                "/hotspot/api/sites/",
                data=json.dumps({"name": "Steal", "domain": "steal.example.com", "key_mode": "same"}),
                content_type="application/json",
            )
        )
        assert unauth_same.status_code == 401

        # 2. User registers Site A via Starlette standalone client (receives scoped session cookie)
        with TestClient(standalone_server.app) as client:
            reg_a = client.post(
                "/hotspot/api/sites/",
                json={"name": "Alpha App", "domain": "alpha.example.com", "key_mode": "new"},
            )
            assert reg_a.status_code == 200
            data_a = reg_a.json()
            assert data_a["status"] == 1
            assert data_a["scope"] == "alpha.example.com"
            site_a = data_a["site"]

            # Trying key_mode='same' pointing at an unowned source_site is rejected
            bad_src = client.post(
                "/hotspot/api/sites/",
                json={
                    "name": "Hijack",
                    "domain": "hijack.example.com",
                    "key_mode": "same",
                    "source_site": "unrelated.example.com",
                },
            )
            assert bad_src.status_code == 403

            # Collision protection still rejects re-registering unrelated domain with key_mode='same'
            collide = client.post(
                "/hotspot/api/sites/",
                json={"name": "Collision", "domain": "unrelated.example.com", "key_mode": "same"},
            )
            assert collide.status_code == 409

            # 3. Logged-in user adds Site B with key_mode='same' -> shares secret_key, keeps both sites in session
            reg_b = client.post(
                "/hotspot/api/sites/",
                json={"name": "Beta Docs", "domain": "beta.example.com", "key_mode": "same"},
            )
            assert reg_b.status_code == 200
            data_b = reg_b.json()
            assert data_b["status"] == 1
            site_b = data_b["site"]
            assert site_b["secret_key"] == site_a["secret_key"]
            assert site_b["api_key"] != site_a["api_key"]
            assert data_b["scope"] == "alpha.example.com,beta.example.com"

            # 4. Logged-in user adds Site C with key_mode='new' -> new secret_key, STILL merged into current session
            reg_c = client.post(
                "/hotspot/api/sites/",
                json={"name": "Gamma Store", "domain": "gamma.example.com", "key_mode": "new"},
            )
            assert reg_c.status_code == 200
            data_c = reg_c.json()
            site_c = data_c["site"]
            assert site_c["secret_key"] != site_a["secret_key"]
            assert data_c["scope"] == "alpha.example.com,beta.example.com,gamma.example.com"

            # Ingest one session per site (including unrelated.example.com)
            now = time.time()
            for idx, (s, host_name) in enumerate(
                [
                    (site_a, "alpha.example.com"),
                    (site_b, "beta.example.com"),
                    (site_c, "gamma.example.com"),
                    (other_site, "unrelated.example.com"),
                ],
                start=1,
            ):
                cap = client.post(
                    "/hotspot/batch/",
                    content=batch_body(
                        [
                            event_item(
                                "$pageview",
                                f"sess-{idx}",
                                now - idx,
                                f"user-{idx}",
                                **{"$current_url": f"https://{host_name}/home", "$host": host_name},
                            )
                        ],
                        api_key=s["api_key"],
                    ),
                    headers={"Content-Type": "text/plain"},
                )
                assert cap.status_code == 200

            # Aggregate overview in current browser session sees Alpha + Beta + Gamma, NEVER Unrelated
            ov_all = client.get("/hotspot/api/overview/").json()
            assert ov_all["scope"]["is_admin"] is False
            assert ov_all["scope"]["authorized_sites"] == [
                "alpha.example.com",
                "beta.example.com",
                "gamma.example.com",
            ]
            assert {s["domain"] for s in ov_all["sites"]} == {
                "alpha.example.com",
                "beta.example.com",
                "gamma.example.com",
            }
            assert ov_all["metrics"]["sessions"] == 3
            assert ov_all["scope"]["sessions_stored"] == 3

            # Per-site switcher query filters cleanly and cross-tenant query yields 0
            ov_b = client.get("/hotspot/api/overview/?site=beta.example.com").json()
            assert ov_b["metrics"]["sessions"] == 1
            assert ov_b["sessions"][0]["session_id"] == "sess-2"

            ov_victim = client.get("/hotspot/api/overview/?site=unrelated.example.com").json()
            assert ov_victim["metrics"]["sessions"] == 0

            # Detail + events feed allow own sites and deny unrelated site
            assert client.get("/hotspot/api/sessions/sess-3/").status_code == 200
            assert client.get("/hotspot/api/sessions/sess-4/").status_code == 404
            feed = client.get("/hotspot/api/events/").json()
            assert len(feed["events"]) == 3
            assert {ev.get("session_id") for ev in feed["events"]} == {"sess-1", "sess-2", "sess-3"}

            # Deleting Site C removes it from cookie scope without dropping Alpha or Beta
            del_unrelated = client.delete(f"/hotspot/api/sites/{other_site['id']}/")
            assert del_unrelated.status_code == 403
            del_c = client.delete(f"/hotspot/api/sites/{site_c['id']}/")
            assert del_c.status_code == 200
            assert del_c.json()["scope"] == "alpha.example.com,beta.example.com"

        # 5. Re-logging in later with shared secret key unlocks BOTH Alpha & Beta; Gamma key unlocks ONLY Gamma
        shared_token = service.login(site_a["secret_key"], domain="alpha.example.com")
        assert shared_token is not None
        shared_ov = service.overview(
            {"hours": "24"},
            authorized_scope=service.authorized_scope(shared_token, None)[1] or "",
        )
        assert {s["domain"] for s in shared_ov["sites"]} == {"alpha.example.com", "beta.example.com"}

        # Admin cookie ("*") MUST NOT be downgraded when admin creates a site
        admin_req = factory.post(
            "/hotspot/api/sites/",
            data=json.dumps(
                {
                    "name": "Admin Shared",
                    "domain": "admin-shared.example.com",
                    "key_mode": "same",
                    "source_site": "alpha.example.com",
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer master-secret-password",
        )
        admin_res = views.sites_endpoint(admin_req)
        assert admin_res.status_code == 200
        admin_payload = json.loads(admin_res.content)
        assert admin_payload["scope"] == "*"
        assert admin_payload["site"]["secret_key"] == site_a["secret_key"]
    finally:
        reset_service(None)


def test_skill_md_download_and_llms_txt() -> None:
    service = make_service(HOTSPOT_SITE_URL="https://hotspot.example.com", HOTSPOT_DASHBOARD_PASSWORD="pw")
    reset_service(service)
    try:
        django_client = Client()
        sk = django_client.get("/skill.md")
        assert sk.status_code == 200
        assert "text/markdown" in sk["Content-Type"]
        body = sk.content.decode("utf-8")
        assert "https://hotspot.example.com/connect/" in body
        assert ".env.hotspot.local" in body
        assert "chmod 600" in body

        dl = django_client.get("/hotspot/skill.md?download=1")
        assert dl.status_code == 200
        assert "hotspot-SKILL.md" in dl["Content-Disposition"]

        llms = django_client.get("/llms.txt").content.decode("utf-8")
        assert "https://hotspot.example.com/skill.md" in llms
        assert "https://hotspot.example.com/connect/" in llms

        st_client = TestClient(standalone_server.app)
        st_sk = st_client.get("/skill.md?download=1")
        assert st_sk.status_code == 200
        assert "hotspot-SKILL.md" in st_sk.headers["content-disposition"]
        assert "https://hotspot.example.com/mcp" in st_sk.text
    finally:
        reset_service(None)


def test_browser_connect_flow_login_register_isolation_and_single_use(tmp_path: Any, monkeypatch: Any) -> None:
    service = make_service(
        HOTSPOT_SITE_URL="https://hotspot.example.com",
        HOTSPOT_DASHBOARD_PASSWORD="master-password",
    )
    reset_service(service)
    try:
        django_client = Client()
        st_client = TestClient(standalone_server.app)

        # Reject unsafe redirect URIs (SSRF / open redirect guard)
        for bad_uri in ("https://evil.example.com/cb", "http://localhost/no-port", "javascript:alert(1)"):
            bad_res = django_client.post(
                "/hotspot/api/connect/start/",
                data=json.dumps({"client_name": "Evil", "redirect_uri": bad_uri}),
                content_type="application/json",
            )
            assert bad_res.status_code == 400

        # Pre-seed unrelated victim domain and owner domain
        _, victim = service.create_site("Victim", "victim.connect.example.com")
        _, owner = service.create_site("Owner App", "owner.connect.example.com")
        assert isinstance(victim, dict) and isinstance(owner, dict)

        # 1. Start Connect session with safe loopback redirect_uri
        start = django_client.post(
            "/hotspot/api/connect/start/",
            data=json.dumps(
                {
                    "client_name": "Claude Code",
                    "domain": "owner.connect.example.com",
                    "redirect_uri": "http://127.0.0.1:43123/cb",
                    "state": "nonce-99",
                }
            ),
            content_type="application/json",
        )
        assert start.status_code == 200
        start_data = start.json()
        user_code = start_data["user_code"]
        device_code = start_data["device_code"]
        assert f"/connect/?code={user_code}" in start_data["verification_uri"]

        # Browser page renders with CSP nonce & status hides secret_key
        page = django_client.get(f"/connect/?code={user_code}")
        assert page.status_code == 200
        assert "Agent Connect" in page.content.decode("utf-8")

        st_status = django_client.get(f"/hotspot/api/connect/status/?code={user_code}").json()
        assert st_status["session"]["client_name"] == "Claude Code"
        assert "secret_key" not in json.dumps(st_status)

        # Pending token poll answers 202
        pending = django_client.post(
            "/hotspot/api/connect/token/",
            data=json.dumps({"device_code": device_code}),
            content_type="application/json",
        )
        assert pending.status_code == 202
        assert pending.json()["pending"] is True

        # Cross-tenant approval attempt while logged into owner MUST return 403
        owner_token = make_dashboard_token(service.config.secret_key, time.time(), scope="owner.connect.example.com")
        django_client.cookies[DASHBOARD_COOKIE] = owner_token
        bad_scope = django_client.post(
            "/hotspot/api/connect/approve/",
            data=json.dumps({"code": user_code, "mode": "existing", "site_id": victim["id"]}),
            content_type="application/json",
        )
        assert bad_scope.status_code == 403
        django_client.cookies.clear()

        # Approve via inline Domain + Secret Key login
        approve = django_client.post(
            "/hotspot/api/connect/approve/",
            data=json.dumps(
                {
                    "code": user_code,
                    "mode": "login",
                    "domain": "owner.connect.example.com",
                    "secret_key": owner["secret_key"],
                }
            ),
            content_type="application/json",
        )
        assert approve.status_code == 200
        app_data = approve.json()
        assert app_data["approved"] is True
        assert app_data["redirect_to"] == f"http://127.0.0.1:43123/cb?code={device_code}&state=nonce-99"
        assert owner["secret_key"] not in app_data["redirect_to"]

        # Double approval is blocked
        re_approve = django_client.post(
            "/hotspot/api/connect/approve/",
            data=json.dumps({"code": user_code, "mode": "existing"}),
            content_type="application/json",
        )
        assert re_approve.status_code == 409

        # Token exchange succeeds ONCE cleanly, then second poll is rejected (single-use)
        ex = django_client.post(
            "/hotspot/api/connect/token/",
            data=json.dumps({"device_code": device_code}),
            content_type="application/json",
        )
        assert ex.status_code == 200
        tok = ex.json()
        assert tok["secret_key"] == owner["secret_key"]
        assert tok["api_key"] == owner["api_key"]
        assert tok["mcp_config"]["mcpServers"]["hotspot"]["headers"]["Authorization"] == f"Bearer {owner['secret_key']}"

        replay_ex = django_client.post(
            "/hotspot/api/connect/token/",
            data=json.dumps({"device_code": device_code}),
            content_type="application/json",
        )
        assert replay_ex.status_code == 404

        # Verify secure local file storage (0600 permissions + .gitignore)
        monkeypatch.chdir(tmp_path)
        saved = save_credentials_bundle(
            tok,
            credentials_path=tmp_path / ".hotspot" / "credentials.json",
            env_path=tmp_path / ".env.hotspot.local",
            mcp_path=tmp_path / ".mcp.json",
        )
        assert set(saved) == {"credentials", "env", "mcp"}
        assert stat.S_IMODE((tmp_path / ".env.hotspot.local").stat().st_mode) == 0o600
        gitignore_text = (tmp_path / ".gitignore").read_text(encoding="utf-8")
        assert ".hotspot/" in gitignore_text and ".env.hotspot.local" in gitignore_text

        # 2. Standalone Starlette server parity: Register a brand-new site during /connect/
        st_start = st_client.post("/hotspot/api/connect/start/", json={"client_name": "Starlette CLI"}).json()
        st_code = st_start["user_code"]
        st_dev = st_start["device_code"]

        # Existing domain collision still requires confirmation_key (409)
        coll = st_client.post(
            "/hotspot/api/connect/approve/",
            json={"code": st_code, "mode": "register", "name": "Hijack", "domain": "victim.connect.example.com"},
        )
        assert coll.status_code == 409

        reg_ok = st_client.post(
            "/hotspot/api/connect/approve/",
            json={"code": st_code, "mode": "register", "name": "New Edge", "domain": "edge.connect.example.com"},
        )
        assert reg_ok.status_code == 200
        assert reg_ok.json()["created_site"]["domain"] == "edge.connect.example.com"

        st_tok = st_client.post("/hotspot/api/connect/token/", json={"device_code": st_dev})
        assert st_tok.status_code == 200
        assert st_tok.json()["domain"] == "edge.connect.example.com"
        assert st_tok.json()["secret_key"].startswith("hs_sec_")
    finally:
        reset_service(None)


def test_overview_api_supports_clarity_filters_via_django_and_starlette() -> None:
    service = make_service(HOTSPOT_DASHBOARD_PASSWORD="pw")
    reset_service(service)
    try:
        ok, site = service.create_site("Clarity Parity", "clarity-parity.example.com")
        assert ok and isinstance(site, dict)
        api_key = site["api_key"]
        secret = site["secret_key"]
        now = time.time()

        # Session 1 + returning Session 2 with UTM campaign, quick back, and web vitals
        service.capture(
            batch_body(
                [
                    event_item(
                        "$pageview", "s-1", now - 3600, "v-ret", **{"$pathname": "/", "$timezone": "America/New_York"}
                    )
                ],
                api_key=api_key,
            ),
            headers={"User-Agent": "Mozilla/5.0"},
        )
        service.capture(
            batch_body(
                [
                    event_item(
                        "$pageview",
                        "s-2",
                        now - 30,
                        "v-ret",
                        **{
                            "$pathname": "/",
                            "$referrer": "https://chatgpt.com/",
                            "$is_ai_referral": True,
                            "$ai_source": "ChatGPT",
                            "utm_campaign": "spring",
                            "utm_source": "ai",
                            "utm_medium": "organic",
                            "$timezone": "America/New_York",
                        },
                    ),
                    event_item("$pageview", "s-2", now - 25, "v-ret", **{"$pathname": "/pricing"}),
                    event_item("$pageview", "s-2", now - 20, "v-ret", **{"$pathname": "/"}),
                    event_item(
                        "$web_vitals",
                        "s-2",
                        now - 18,
                        "v-ret",
                        **{"$pathname": "/", "$lcp_ms": 1400, "$inp_ms": 110, "$cls": 0.03},
                    ),
                    event_item("plan_selected", "s-2", now - 15, "v-ret", **{"$pathname": "/pricing"}),
                ],
                api_key=api_key,
            ),
            headers={"User-Agent": "Mozilla/5.0"},
        )

        django_client = Client()
        auth_headers = {"HTTP_AUTHORIZATION": f"Bearer {secret}"}
        dj_resp = django_client.get(
            "/hotspot/api/overview/?hours=24&channel=ai&country=US&status=quick_back",
            **auth_headers,
        )
        assert dj_resp.status_code == 200
        dj_data = dj_resp.json()
        assert [s["session_id"] for s in dj_data["sessions"]] == ["s-2"]
        assert dj_data["users_overview"]["returning_sessions"] == 1
        assert dj_data["quick_backs"]["count"] == 1
        assert dj_data["performance"]["score"] == 100
        assert dj_data["referrers"]["campaigns"][0]["campaign"] == "spring"
        assert any(ev["name"] == "plan_selected" for ev in dj_data["smart_events"])

        st_client = TestClient(standalone_server.app)
        st_resp = st_client.get(
            "/hotspot/api/overview/?hours=24&channel=ai&country=US&status=returning",
            headers={"Authorization": f"Bearer {secret}"},
        )
        assert st_resp.status_code == 200
        st_data = st_resp.json()
        assert [s["session_id"] for s in st_data["sessions"]] == ["s-2"]
        assert st_data["scope"]["channel"] == "ai"
        assert st_data["scope"]["country"] == "US"
    finally:
        reset_service(None)
