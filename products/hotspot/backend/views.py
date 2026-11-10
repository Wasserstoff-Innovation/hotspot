"""Django entrypoints. Each one reads the request, calls the service and shapes the response."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from products.hotspot.backend.auth import DASHBOARD_COOKIE, TOKEN_TTL_SECONDS
from products.hotspot.backend.security import (
    API_CSP,
    CAPTURE_CORS_HEADERS,
    NO_STORE_HEADERS,
    generate_nonce,
    is_same_site_form_post,
    page_csp,
)
from products.hotspot.backend.service import FAVICON_SVG, HotspotService, get_service

DASHBOARD_PATH = "/dashboard/"
LOGIN_PATH = "/dashboard/login/"
ASSET_CACHE_CONTROL = "public, max-age=86400"


def landing(request: HttpRequest) -> HttpResponse:
    return _page(request, "landing")


def demo(request: HttpRequest, view: str = "") -> HttpResponse:
    if not get_service().config.demo_enabled:
        return HttpResponse("The sample site is disabled on this deployment.", status=404, content_type="text/plain")
    return _page(request, "demo")


def dashboard(request: HttpRequest) -> HttpResponse:
    if not _authorized(request):
        return HttpResponseRedirect(LOGIN_PATH)
    response = _page(request, "dashboard", replay=True)
    _apply(response, NO_STORE_HEADERS)
    return response


def dashboard_redirect(request: HttpRequest) -> HttpResponse:
    return HttpResponseRedirect(DASHBOARD_PATH)


@csrf_exempt
def login(request: HttpRequest) -> HttpResponse:
    service = get_service()
    if not service.config.dashboard_protected or _authorized(request):
        return HttpResponseRedirect(DASHBOARD_PATH)
    if request.method == "GET":
        return _page(request, "login", error="")
    if request.method != "POST":
        return HttpResponseNotAllowed(["GET", "POST"])
    if not is_same_site_form_post(request.headers):
        return HttpResponse("Cross-site form posts are refused.", status=403, content_type="text/plain")
    token = service.login(request.POST.get("password"))
    if token is None:
        response = _page(request, "login", error="That password is not right. Try again.")
        response.status_code = 401
        return response
    response = HttpResponseRedirect(DASHBOARD_PATH)
    response.set_cookie(
        DASHBOARD_COOKIE,
        token,
        max_age=TOKEN_TTL_SECONDS,
        httponly=True,
        samesite="Lax",
        secure=request.is_secure(),
        path="/",
    )
    return response


@csrf_exempt
def logout(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    response = HttpResponseRedirect(LOGIN_PATH if get_service().config.dashboard_protected else DASHBOARD_PATH)
    response.delete_cookie(DASHBOARD_COOKIE, path="/")
    return response


def sdk(request: HttpRequest) -> HttpResponse:
    response = HttpResponse(get_service().sdk_source(), content_type="application/javascript; charset=utf-8")
    response["Cache-Control"] = "public, max-age=3600"
    _apply(response, CAPTURE_CORS_HEADERS)
    return response


def asset(request: HttpRequest, name: str) -> HttpResponse:
    found = get_service().asset(name)
    if found is None:
        return HttpResponse("Not found", status=404, content_type="text/plain")
    body, content_type = found
    response = HttpResponse(body, content_type=content_type)
    response["Cache-Control"] = ASSET_CACHE_CONTROL
    _apply(response, CAPTURE_CORS_HEADERS)
    return response


def llms_txt(request: HttpRequest) -> HttpResponse:
    response = HttpResponse(get_service().llms_txt(), content_type="text/plain; charset=utf-8")
    response["Cache-Control"] = ASSET_CACHE_CONTROL
    return response


def favicon(request: HttpRequest) -> HttpResponse:
    response = HttpResponse(FAVICON_SVG, content_type="image/svg+xml")
    response["Cache-Control"] = ASSET_CACHE_CONTROL
    return response


@csrf_exempt
def capture(request: HttpRequest) -> HttpResponse:
    return _capture(request, lambda: get_service().capture(request.body, request.headers))


@csrf_exempt
def capture_recording(request: HttpRequest) -> HttpResponse:
    return _capture(request, lambda: get_service().capture_recording(request.body, _params(request)))


def overview(request: HttpRequest) -> HttpResponse:
    if not _authorized(request):
        return _unauthorized()
    return _json(get_service().overview(_params(request)))


@csrf_exempt
def sites_endpoint(request: HttpRequest) -> HttpResponse:
    if not _authorized(request):
        return _unauthorized()
    service = get_service()
    if request.method == "POST":
        try:
            payload = json.loads(request.body.decode("utf-8")) if request.body else {}
        except Exception:
            payload = {}
        name = str(payload.get("name") or "").strip()
        domain = str(payload.get("domain") or "").strip()
        if not domain:
            return _json({"status": 0, "error": "domain is required"}, status=400)
        site = service.create_site(name or domain, domain)
        return _json({"status": 1, "site": site})
    return _json({"status": 1, "sites": service.list_sites()})


@csrf_exempt
def site_delete_endpoint(request: HttpRequest, site_id: str) -> HttpResponse:
    if not _authorized(request):
        return _unauthorized()
    ok = get_service().delete_site(site_id)
    return _json({"status": 1 if ok else 0})


def session_detail(request: HttpRequest, session_id: str) -> HttpResponse:
    if not _authorized(request):
        return _unauthorized()
    detail = get_service().session_detail(session_id)
    if detail is None:
        return _json({"status": 0, "error": "Session not found"}, status=404)
    return _json(detail)


def session_recording(request: HttpRequest, session_id: str) -> HttpResponse:
    if not _authorized(request):
        return _unauthorized()
    body = get_service().recording(session_id, _params(request))
    if body is None:
        return _json({"status": 0, "error": "Session not found"}, status=404)
    return _api_response(HttpResponse(body, content_type="application/json"))


def events_feed(request: HttpRequest) -> HttpResponse:
    if not _authorized(request):
        return _unauthorized()
    return _json(get_service().events_feed(_params(request)))


def _capture(request: HttpRequest, handle: Callable[[], tuple[int, dict[str, Any]]]) -> HttpResponse:
    if request.method == "OPTIONS":
        response = HttpResponse(status=204)
    elif request.method != "POST":
        response = JsonResponse({"status": 0, "error": "Use POST"}, status=405)
    else:
        status, payload = handle()
        response = JsonResponse(payload, status=status)
    _apply(response, CAPTURE_CORS_HEADERS)
    response["Content-Security-Policy"] = API_CSP
    return response


def _page(request: HttpRequest, page: str, replay: bool = False, **extra: str) -> HttpResponse:
    nonce = getattr(request, "csp_nonce", None) or generate_nonce()
    context = get_service().page_context(page, nonce, **extra)
    response = render(request, f"hotspot/{page}.html", context)
    # Set here so the app-wide policy leaves this document alone; it would refuse the page's inline script.
    response["Content-Security-Policy"] = page_csp(nonce, replay=replay)
    return response


def _authorized(request: HttpRequest) -> bool:
    service: HotspotService = get_service()
    return service.is_authorized(request.COOKIES.get(DASHBOARD_COOKIE), request.headers.get("Authorization"))


def _unauthorized() -> HttpResponse:
    return _json({"status": 0, "error": "Sign in to the dashboard first"}, status=401)


def _json(payload: dict[str, object], status: int = 200) -> HttpResponse:
    return _api_response(JsonResponse(payload, status=status))


def _api_response(response: HttpResponse) -> HttpResponse:
    _apply(response, NO_STORE_HEADERS)
    response["Content-Security-Policy"] = API_CSP
    return response


def _params(request: HttpRequest) -> dict[str, str]:
    return {key: request.GET.get(key, "") for key in request.GET}


def _apply(response: HttpResponse, headers: Mapping[str, str]) -> None:
    for name, value in headers.items():
        response[name] = value
