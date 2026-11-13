"""Django entrypoints. Each one reads the request, calls the service and shapes the response."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from typing import Any

from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from products.hotspot.backend.auth import DASHBOARD_COOKIE, TOKEN_TTL_SECONDS, verify_dashboard_token
from products.hotspot.backend.mcp import McpAuthError, handle_mcp_http_payload, mcp_discovery_document, resolve_mcp_auth
from products.hotspot.backend.security import (
    API_CSP,
    CAPTURE_CORS_HEADERS,
    MCP_CORS_HEADERS,
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

    secret_or_password = request.POST.get("password") or request.POST.get("secret_key")
    domain = request.POST.get("domain")
    if not secret_or_password and request.body:
        try:
            payload = json.loads(request.body.decode("utf-8"))
            secret_or_password = payload.get("password") or payload.get("secret_key")
            domain = payload.get("domain")
        except Exception:
            pass

    token = service.login(secret_or_password, domain)
    if token is None:
        if request.headers.get("Accept") == "application/json" or request.content_type == "application/json":
            return _json({"status": 0, "error": "Invalid domain or secret key. Try again."}, status=401)
        response = _page(request, "login", error="Invalid domain or secret key. Try again.")
        response.status_code = 401
        return response

    scope = verify_dashboard_token(service.config.secret_key, token, time.time()) or "*"

    if request.headers.get("Accept") == "application/json" or request.content_type == "application/json":
        response = _json({"status": 1, "scope": scope, "redirect": DASHBOARD_PATH})
    else:
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


def skill_md(request: HttpRequest) -> HttpResponse:
    response = HttpResponse(get_service().skill_markdown(), content_type="text/markdown; charset=utf-8")
    response["Cache-Control"] = "public, max-age=300"
    _apply(response, MCP_CORS_HEADERS)
    if (request.GET.get("download") or "").lower() in ("1", "true", "yes"):
        response["Content-Disposition"] = 'attachment; filename="hotspot-SKILL.md"'
    return response


def connect_page(request: HttpRequest) -> HttpResponse:
    response = _page(request, "connect")
    _apply(response, NO_STORE_HEADERS)
    return response


def docs(request: HttpRequest, slug: str | None = None) -> HttpResponse:
    nonce = getattr(request, "csp_nonce", None) or generate_nonce()
    ctx = get_service().docs_context(slug, nonce)
    response = render(request, "hotspot/docs.html", ctx)
    response["Content-Security-Policy"] = page_csp(nonce, replay=False)
    response["Cache-Control"] = "public, max-age=3600"
    return response


def sitemap_xml(request: HttpRequest) -> HttpResponse:
    xml = get_service().sitemap_xml()
    response = HttpResponse(xml, content_type="application/xml; charset=utf-8")
    response["Cache-Control"] = "public, max-age=86400"
    return response


def robots_txt(request: HttpRequest) -> HttpResponse:
    txt = get_service().robots_txt()
    response = HttpResponse(txt, content_type="text/plain; charset=utf-8")
    response["Cache-Control"] = "public, max-age=86400"
    return response


def _json_body(request: HttpRequest) -> dict[str, Any]:
    if not request.body:
        return {}
    try:
        parsed = json.loads(request.body.decode("utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _set_dashboard_cookie(response: HttpResponse, token: str, *, secure: bool) -> None:
    response.set_cookie(
        DASHBOARD_COOKIE,
        token,
        max_age=TOKEN_TTL_SECONDS,
        httponly=True,
        samesite="Lax",
        secure=secure,
        path="/",
    )


@csrf_exempt
def connect_start_endpoint(request: HttpRequest) -> HttpResponse:
    if request.method == "OPTIONS":
        return HttpResponse(status=204, headers=MCP_CORS_HEADERS)
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST", "OPTIONS"])
    payload = _json_body(request)
    status_code, body = get_service().start_connect_session(
        client_name=str(payload.get("client_name") or "AI Assistant"),
        domain_hint=str(payload.get("domain") or ""),
        name_hint=str(payload.get("name") or ""),
        redirect_uri=str(payload.get("redirect_uri") or ""),
        state=str(payload.get("state") or ""),
    )
    response = _json(body, status=status_code)
    _apply(response, MCP_CORS_HEADERS)
    return response


def connect_status_endpoint(request: HttpRequest) -> HttpResponse:
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    status_code, body = get_service().connect_session_status(
        request.GET.get("code") or "",
        cookie_token=request.COOKIES.get(DASHBOARD_COOKIE),
        authorization=request.headers.get("Authorization"),
    )
    return _json(body, status=status_code)


@csrf_exempt
def connect_approve_endpoint(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if not is_same_site_form_post(request.headers):
        return _json({"status": 0, "error": "Cross-site connect approval requests are refused."}, status=403)
    payload = _json_body(request)
    status_code, body = get_service().approve_connect_session(
        str(payload.get("code") or payload.get("user_code") or ""),
        mode=str(payload.get("mode") or "existing"),
        site_id=str(payload.get("site_id") or "") or None,
        domain=str(payload.get("domain") or ""),
        name=str(payload.get("name") or ""),
        secret_key=str(payload.get("secret_key") or ""),
        password=str(payload.get("password") or ""),
        confirmation_key=str(payload.get("confirmation_key") or "") or None,
        key_mode=str(payload.get("key_mode") or "new"),
        source_site=str(payload.get("source_site") or "") or None,
        cookie_token=request.COOKIES.get(DASHBOARD_COOKIE),
        authorization=request.headers.get("Authorization"),
    )
    token = body.pop("token", None) if status_code == 200 else None
    response = _json(body, status=status_code)
    if token:
        _set_dashboard_cookie(response, str(token), secure=request.is_secure())
    return response


@csrf_exempt
def connect_token_endpoint(request: HttpRequest) -> HttpResponse:
    if request.method == "OPTIONS":
        return HttpResponse(status=204, headers=MCP_CORS_HEADERS)
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST", "OPTIONS"])
    payload = _json_body(request)
    status_code, body = get_service().exchange_connect_token(str(payload.get("device_code") or ""))
    response = _json(body, status=status_code)
    _apply(response, MCP_CORS_HEADERS)
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
    ok, scope = _authorized_scope(request)
    if not ok:
        return _unauthorized()
    return _json(get_service().overview(_params(request), authorized_scope=scope))


@csrf_exempt
def sites_endpoint(request: HttpRequest) -> HttpResponse:
    service = get_service()
    if request.method == "POST":
        try:
            payload = json.loads(request.body.decode("utf-8")) if request.body else {}
        except Exception:
            payload = {}
        status_code, body = service.create_site_for_session(
            str(payload.get("name") or ""),
            str(payload.get("domain") or ""),
            confirmation_key=str(payload.get("confirmation_key") or "") or None,
            key_mode=str(payload.get("key_mode") or "new"),
            source_site=str(payload.get("source_site") or payload.get("current_site") or "") or None,
            cookie_token=request.COOKIES.get(DASHBOARD_COOKIE),
            authorization=request.headers.get("Authorization"),
        )
        response = _json(body, status=status_code)
        if status_code == 200 and body.get("token"):
            response.set_cookie(
                DASHBOARD_COOKIE,
                str(body["token"]),
                max_age=TOKEN_TTL_SECONDS,
                httponly=True,
                samesite="Lax",
                secure=request.is_secure(),
                path="/",
            )
        return response

    ok, scope = _authorized_scope(request)
    if not ok:
        return _unauthorized()
    return _json({"status": 1, "sites": service.list_sites(authorized_scope=scope)})


@csrf_exempt
def site_delete_endpoint(request: HttpRequest, site_id: str) -> HttpResponse:
    ok, scope = _authorized_scope(request)
    if not ok:
        return _unauthorized()
    status_code, body, refreshed_token = get_service().delete_site_for_session(site_id, scope)
    response = _json(body, status=status_code)
    if refreshed_token is not None:
        if refreshed_token:
            response.set_cookie(
                DASHBOARD_COOKIE,
                refreshed_token,
                max_age=TOKEN_TTL_SECONDS,
                httponly=True,
                samesite="Lax",
                secure=request.is_secure(),
                path="/",
            )
        else:
            response.delete_cookie(DASHBOARD_COOKIE, path="/")
    return response


def session_detail(request: HttpRequest, session_id: str) -> HttpResponse:
    ok, scope = _authorized_scope(request)
    if not ok:
        return _unauthorized()
    detail = get_service().session_detail(session_id, authorized_scope=scope, site=(request.GET.get("site") or None))
    if detail is None:
        return _json({"status": 0, "error": "Session not found"}, status=404)
    return _json(detail)


def session_recording(request: HttpRequest, session_id: str) -> HttpResponse:
    ok, scope = _authorized_scope(request)
    if not ok:
        return _unauthorized()
    body = get_service().recording(session_id, _params(request), authorized_scope=scope)
    if body is None:
        return _json({"status": 0, "error": "Session not found"}, status=404)
    return _api_response(HttpResponse(body, content_type="application/json"))


def events_feed(request: HttpRequest) -> HttpResponse:
    ok, scope = _authorized_scope(request)
    if not ok:
        return _unauthorized()
    return _json(get_service().events_feed(_params(request), authorized_scope=scope))


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


def _authorized_scope(request: HttpRequest) -> tuple[bool, str]:
    service: HotspotService = get_service()
    ok, scope = service.authorized_scope(request.COOKIES.get(DASHBOARD_COOKIE), request.headers.get("Authorization"))
    return ok, scope or "*"


def _authorized(request: HttpRequest) -> bool:
    ok, _ = _authorized_scope(request)
    return ok


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


@csrf_exempt
def mcp_endpoint(request: HttpRequest) -> HttpResponse:
    """Streamable HTTP MCP endpoint for Django deployments."""
    headers = {**MCP_CORS_HEADERS, **NO_STORE_HEADERS}
    if request.method == "OPTIONS":
        return HttpResponse(status=204, headers=MCP_CORS_HEADERS)

    service = get_service()
    if request.method == "DELETE":
        return JsonResponse({"status": 1, "closed": True}, headers=headers)

    if request.method == "GET":
        try:
            resolve_mcp_auth(service, headers=request.headers, query_params=request.GET, require=False)
        except McpAuthError as exc:
            err_headers = {**headers, "WWW-Authenticate": 'Bearer realm="hotspot-mcp"'}
            return JsonResponse(
                {"jsonrpc": "2.0", "error": {"code": -32001, "message": str(exc)}, "id": None},
                status=401,
                headers=err_headers,
            )
        accept = (request.headers.get("accept") or "").lower()
        if "text/event-stream" in accept and "application/json" not in accept and "text/html" not in accept:
            return HttpResponse(status=405, headers={"Allow": "POST, OPTIONS", **MCP_CORS_HEADERS})
        return JsonResponse(mcp_discovery_document(service), headers=headers)

    if request.method != "POST":
        return HttpResponse(status=405, headers={"Allow": "GET, POST, DELETE, OPTIONS", **MCP_CORS_HEADERS})

    status, payload = handle_mcp_http_payload(
        request.body,
        service=service,
        headers=request.headers,
        query_params=request.GET,
    )
    if status == 401:
        headers = {**headers, "WWW-Authenticate": 'Bearer realm="hotspot-mcp"'}
    if status == 202 or payload is None:
        return HttpResponse(status=202, headers=headers)
    accept = (request.headers.get("accept") or "").lower()
    if status == 200 and "text/event-stream" in accept and "application/json" not in accept:
        body = f"event: message\ndata: {json.dumps(payload, default=str)}\n\n"
        return HttpResponse(body, status=200, content_type="text/event-stream; charset=utf-8", headers=headers)
    return JsonResponse(payload, status=status, safe=False, headers=headers)
