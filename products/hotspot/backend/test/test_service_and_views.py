from __future__ import annotations

import json
from collections.abc import Iterator

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
