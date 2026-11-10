from __future__ import annotations

import gzip
import json
import time
from collections.abc import Iterator
from typing import Any

import pytest

from django.test import RequestFactory

from products.hotspot.backend import views
from products.hotspot.backend.auth import DASHBOARD_COOKIE, make_dashboard_token, verify_dashboard_token
from products.hotspot.backend.service import HotspotService, reset_service
from products.hotspot.backend.templating import render_string
from products.hotspot.backend.test.helpers import batch_body, event_item, make_service

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
