"""Hotspot standalone server: the same product without the PostHog stack.

Runs on Starlette and Uvicorn, stores sessions in Redis when REDIS_URL is set and in
memory otherwise. Every handler is a thin wrapper around products.hotspot.backend.service.
"""

from __future__ import annotations

import os
import sys
import json
import time
import uuid
import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from starlette.routing import Route

from products.hotspot.backend.mcp import McpAuthError, handle_mcp_http_payload, mcp_discovery_document, resolve_mcp_auth
from products.hotspot.backend.security import MCP_CORS_HEADERS, NO_STORE_HEADERS

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from products.hotspot.backend.auth import DASHBOARD_COOKIE, TOKEN_TTL_SECONDS, verify_dashboard_token  # noqa: E402
from products.hotspot.backend.security import (  # noqa: E402
    API_CSP,
    CAPTURE_CORS_HEADERS,
    generate_nonce,
    is_same_site_form_post,
    page_csp,
)
from products.hotspot.backend.service import FAVICON_SVG, HotspotService, get_service  # noqa: E402
from products.hotspot.backend.templating import render_template  # noqa: E402

DASHBOARD_PATH = "/dashboard/"
LOGIN_PATH = "/dashboard/login/"
ASSET_CACHE_CONTROL = "public, max-age=86400"


def _service() -> HotspotService:
    return get_service()


def _authorized_scope(request: Request) -> tuple[bool, str]:
    service = _service()
    ok, scope = service.authorized_scope(request.cookies.get(DASHBOARD_COOKIE), request.headers.get("authorization"))
    return ok, scope or "*"


def _authorized(request: Request) -> bool:
    ok, _ = _authorized_scope(request)
    return ok


def _page(request: Request, page: str, replay: bool = False, status: int = 200, **extra: str) -> Response:
    nonce = generate_nonce()
    html = render_template(f"{page}.html", _service().page_context(page, nonce, **extra))
    response = HTMLResponse(html, status_code=status)
    response.headers["Content-Security-Policy"] = page_csp(nonce, replay=replay)
    return response


def _api_response(response: Response) -> Response:
    response.headers.update(NO_STORE_HEADERS)
    response.headers["Content-Security-Policy"] = API_CSP
    return response


def _json(payload: dict[str, object], status: int = 200) -> Response:
    return _api_response(JSONResponse(payload, status_code=status))


async def landing(request: Request) -> Response:
    return _page(request, "landing")


async def demo(request: Request) -> Response:
    if not _service().config.demo_enabled:
        return PlainTextResponse("The sample site is disabled on this deployment.", status_code=404)
    return _page(request, "demo")


async def dashboard(request: Request) -> Response:
    if not _authorized(request):
        return RedirectResponse(LOGIN_PATH, status_code=302)
    response = _page(request, "dashboard", replay=True)
    response.headers.update(NO_STORE_HEADERS)
    return response


async def app_redirect(request: Request) -> Response:
    return RedirectResponse(DASHBOARD_PATH, status_code=302)


async def login(request: Request) -> Response:
    service = _service()
    if request.method in ("GET", "HEAD"):
        if _authorized(request):
            return RedirectResponse(DASHBOARD_PATH, status_code=302)
        return _page(
            request,
            "login",
            error="",
            api_key=service.config.api_key_for_pages,
            site_url=service.config.site_url,
            sdk_url=service.config.sdk_url,
        )
    if not is_same_site_form_post(request.headers):
        return PlainTextResponse("Cross-site form posts are refused.", status_code=403)

    domain = None
    secret_or_password = None
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            data = await request.json()
            domain = data.get("domain")
            secret_or_password = data.get("password") or data.get("secret_key")
        except Exception:
            pass
    else:
        form = await request.form()
        password_val = form.get("password")
        secret_val = form.get("secret_key")
        domain_val = form.get("domain")
        secret_or_password = str(password_val or secret_val or "") if (password_val or secret_val) else None
        domain = str(domain_val) if domain_val else None

    token = service.login(secret_or_password, domain)
    scope = verify_dashboard_token(service.config.secret_key, token, time.time()) or "*" if token else None
    is_json = "application/json" in request.headers.get("accept", "") or "application/json" in content_type

    if token is None:
        if is_json:
            return _json({"status": 0, "error": "Invalid domain or secret key. Try again."}, status=401)
        return _page(
            request,
            "login",
            status=401,
            error="Invalid domain or secret key. Try again.",
            api_key=service.config.api_key_for_pages,
            site_url=service.config.site_url,
            sdk_url=service.config.sdk_url,
        )

    if is_json:
        response = _json({"status": 1, "scope": scope, "redirect": DASHBOARD_PATH})
    else:
        response = RedirectResponse(DASHBOARD_PATH, status_code=303)

    response.set_cookie(
        DASHBOARD_COOKIE,
        token,
        max_age=TOKEN_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )
    return response


async def logout(request: Request) -> Response:
    target = LOGIN_PATH if _service().config.dashboard_protected else DASHBOARD_PATH
    response = RedirectResponse(target, status_code=303)
    response.delete_cookie(DASHBOARD_COOKIE, path="/")
    return response


async def sdk(request: Request) -> Response:
    response = Response(_service().sdk_source(), media_type="application/javascript; charset=utf-8")
    response.headers["Cache-Control"] = "public, max-age=3600"
    response.headers.update(CAPTURE_CORS_HEADERS)
    return response


async def asset(request: Request) -> Response:
    found = _service().asset(request.path_params["name"])
    if found is None:
        return PlainTextResponse("Not found", status_code=404)
    body, content_type = found
    response = Response(body, media_type=content_type)
    response.headers["Cache-Control"] = ASSET_CACHE_CONTROL
    response.headers.update(CAPTURE_CORS_HEADERS)
    return response


async def llms_txt(request: Request) -> Response:
    response = PlainTextResponse(_service().llms_txt())
    response.headers["Cache-Control"] = ASSET_CACHE_CONTROL
    return response


async def favicon(request: Request) -> Response:
    response = Response(FAVICON_SVG, media_type="image/svg+xml")
    response.headers["Cache-Control"] = ASSET_CACHE_CONTROL
    return response


async def _capture(request: Request, handle: Callable[[bytes], tuple[int, dict[str, Any]]]) -> Response:
    if request.method == "OPTIONS":
        response: Response = Response(status_code=204)
    else:
        status, payload = handle(await request.body())
        response = JSONResponse(payload, status_code=status)
    response.headers.update(CAPTURE_CORS_HEADERS)
    response.headers["Content-Security-Policy"] = API_CSP
    return response


async def capture(request: Request) -> Response:
    return await _capture(request, lambda body: _service().capture(body, request.headers))


async def capture_recording(request: Request) -> Response:
    return await _capture(request, lambda body: _service().capture_recording(body, dict(request.query_params)))


async def overview(request: Request) -> Response:
    ok, scope = _authorized_scope(request)
    if not ok:
        return _unauthorized()
    return _json(_service().overview(dict(request.query_params), authorized_scope=scope))


async def session_detail(request: Request) -> Response:
    ok, scope = _authorized_scope(request)
    if not ok:
        return _unauthorized()
    detail = _service().session_detail(request.path_params["session_id"], authorized_scope=scope)
    if detail is None:
        return _json({"status": 0, "error": "Session not found"}, status=404)
    return _json(detail)


async def session_recording(request: Request) -> Response:
    ok, scope = _authorized_scope(request)
    if not ok:
        return _unauthorized()
    body = _service().recording(request.path_params["session_id"], dict(request.query_params), authorized_scope=scope)
    if body is None:
        return _json({"status": 0, "error": "Session not found"}, status=404)
    return _api_response(Response(body, media_type="application/json"))


async def events_feed(request: Request) -> Response:
    ok, scope = _authorized_scope(request)
    if not ok:
        return _unauthorized()
    return _json(_service().events_feed(dict(request.query_params), authorized_scope=scope))


async def sites_endpoint(request: Request) -> Response:
    service = _service()
    if request.method == "POST":
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        name = str(payload.get("name") or "").strip()
        domain = str(payload.get("domain") or "").strip()
        confirmation_key = str(payload.get("confirmation_key") or "").strip() or None
        if not domain:
            return _json({"status": 0, "error": "domain is required"}, status=400)
        ok, res = service.create_site(name or domain, domain, confirmation_key=confirmation_key)
        if not ok:
            return _json({"status": 0, "error": str(res)}, status=409)

        token = service.login(res.get("secret_key"), domain=res.get("domain")) if isinstance(res, dict) else None
        response = _json({"status": 1, "site": res, "token": token})
        if token:
            response.set_cookie(
                DASHBOARD_COOKIE,
                token,
                max_age=TOKEN_TTL_SECONDS,
                httponly=True,
                samesite="lax",
                secure=request.url.scheme == "https",
                path="/",
            )
        return response

    ok, scope = _authorized_scope(request)
    if not ok:
        return _unauthorized()
    return _json({"status": 1, "sites": service.list_sites(authorized_scope=scope)})


async def site_delete_endpoint(request: Request) -> Response:
    ok, scope = _authorized_scope(request)
    if not ok:
        return _unauthorized()
    service = _service()
    site_id = request.path_params.get("site_id", "")
    if scope != "*":
        site = service.store.get_site_by_domain(scope)
        if not site or site.get("id") != site_id:
            return _json({"status": 0, "error": "Unauthorized to delete this site"}, status=403)
    ok_del = service.delete_site(site_id)
    return _json({"status": 1 if ok_del else 0})
    ok = _service().delete_site(site_id)
    return _json({"status": 1 if ok else 0})


def _unauthorized() -> Response:
    return _json({"status": 0, "error": "Sign in to the dashboard first"}, status=401)


_MCP_SSE_QUEUES: dict[str, tuple[Any, dict[str, str], dict[str, str]]] = {}


async def mcp_endpoint(request: Request) -> Response:
    """Streamable HTTP MCP endpoint (`/mcp`)."""
    headers = {**MCP_CORS_HEADERS, **NO_STORE_HEADERS}
    if request.method == "OPTIONS":
        return Response(status_code=204, headers=MCP_CORS_HEADERS)

    service = _service()
    if request.method == "DELETE":
        return JSONResponse({"status": 1, "closed": True}, headers=headers)

    req_headers = dict(request.headers)
    req_query = dict(request.query_params)

    if request.method == "GET":
        try:
            resolve_mcp_auth(service, headers=req_headers, query_params=req_query, require=False)
        except McpAuthError as exc:
            return JSONResponse(
                {"jsonrpc": "2.0", "error": {"code": -32001, "message": str(exc)}, "id": None},
                status_code=401,
                headers={**headers, "WWW-Authenticate": 'Bearer realm="hotspot-mcp"'},
            )
        accept = (request.headers.get("accept") or "").lower()
        if "text/event-stream" in accept and "text/html" not in accept:
            return Response(status_code=405, headers={"Allow": "POST, OPTIONS", **MCP_CORS_HEADERS})
        return JSONResponse(mcp_discovery_document(service), headers=headers)

    body = await request.body()
    status, payload = handle_mcp_http_payload(
        body,
        service=service,
        headers=req_headers,
        query_params=req_query,
    )
    if status == 401:
        headers = {**headers, "WWW-Authenticate": 'Bearer realm="hotspot-mcp"'}
    if status == 202 or payload is None:
        return Response(status_code=202, headers=headers)

    accept = (request.headers.get("accept") or "").lower()
    if status == 200 and "text/event-stream" in accept and "application/json" not in accept:
        sse_body = f"event: message\ndata: {json.dumps(payload, default=str)}\n\n"
        return Response(
            content=sse_body,
            status_code=200,
            media_type="text/event-stream",
            headers={**headers, "Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    return JSONResponse(payload, status_code=status, headers=headers)


async def mcp_sse_endpoint(request: Request) -> Response:
    """Legacy SSE MCP stream endpoint (`/mcp/sse`)."""
    if request.method == "OPTIONS":
        return Response(status_code=204, headers=MCP_CORS_HEADERS)

    service = _service()
    req_headers = dict(request.headers)
    req_query = dict(request.query_params)
    try:
        resolve_mcp_auth(service, headers=req_headers, query_params=req_query, require=False)
    except McpAuthError as exc:
        return JSONResponse(
            {"jsonrpc": "2.0", "error": {"code": -32001, "message": str(exc)}, "id": None},
            status_code=401,
            headers={**MCP_CORS_HEADERS, **NO_STORE_HEADERS, "WWW-Authenticate": 'Bearer realm="hotspot-mcp"'},
        )

    if len(_MCP_SSE_QUEUES) >= 512:
        oldest_sid = next(iter(_MCP_SSE_QUEUES))
        old_q, _, _ = _MCP_SSE_QUEUES.pop(oldest_sid)
        try:
            old_q.put_nowait(None)
        except asyncio.QueueFull:
            pass

    session_id = uuid.uuid4().hex
    queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=256)
    _MCP_SSE_QUEUES[session_id] = (queue, req_headers, req_query)
    endpoint_uri = f"/mcp/messages/?session_id={session_id}"

    async def _stream() -> AsyncIterator[str]:
        try:
            yield f"event: endpoint\ndata: {endpoint_uri}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=20.0)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                if msg is None:
                    break
                yield f"event: message\ndata: {msg}\n\n"
        finally:
            _MCP_SSE_QUEUES.pop(session_id, None)

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            **MCP_CORS_HEADERS,
        },
    )


async def mcp_messages_endpoint(request: Request) -> Response:
    """Legacy SSE MCP POST messages endpoint (`/mcp/messages/`)."""
    headers = {**MCP_CORS_HEADERS, **NO_STORE_HEADERS}
    if request.method == "OPTIONS":
        return Response(status_code=204, headers=MCP_CORS_HEADERS)

    session_id = (request.query_params.get("session_id") or "").strip()
    entry = _MCP_SSE_QUEUES.get(session_id)
    if entry is None:
        return JSONResponse(
            {"error": "Unknown or expired MCP SSE session_id"},
            status_code=404,
            headers=headers,
        )

    queue, conn_headers, conn_query = entry
    post_headers = dict(request.headers)
    merged_headers = {**conn_headers}
    for key in ("authorization", "x-hotspot-secret-key", "x-api-key", "accept", "content-type"):
        if key in post_headers:
            merged_headers[key] = post_headers[key]
    merged_query = {**conn_query, **dict(request.query_params)}

    body = await request.body()
    status, payload = handle_mcp_http_payload(
        body,
        service=_service(),
        headers=merged_headers,
        query_params=merged_query,
    )
    if status == 401:
        return JSONResponse(
            payload,
            status_code=401,
            headers={**headers, "WWW-Authenticate": 'Bearer realm="hotspot-mcp"'},
        )
    if status == 400:
        return JSONResponse(payload, status_code=400, headers=headers)
    if payload is not None:
        await queue.put(json.dumps(payload, default=str))
    return Response(status_code=202, headers=headers)


routes = [
    Route("/", landing),
    Route("/login/", login, methods=["GET", "POST", "HEAD"]),
    Route("/login", login, methods=["GET", "POST", "HEAD"]),
    Route("/demo/", demo),
    Route("/demo/{view}/", demo),
    Route("/demo/{view}", demo),
    Route("/dashboard/", dashboard),
    Route("/dashboard/login/", login, methods=["GET", "POST"]),
    Route("/dashboard/logout/", logout, methods=["POST"]),
    Route("/app/", app_redirect),
    Route("/hotspot.js", sdk),
    Route("/hotspot/assets/{name}", asset),
    Route("/llms.txt", llms_txt),
    Route("/favicon.ico", favicon),
    Route("/hotspot/batch/", capture, methods=["POST", "OPTIONS"]),
    Route("/hotspot/batch", capture, methods=["POST", "OPTIONS"]),
    Route("/hotspot/replay/", capture_recording, methods=["POST", "OPTIONS"]),
    Route("/hotspot/replay", capture_recording, methods=["POST", "OPTIONS"]),
    Route("/hotspot/api/overview/", overview),
    Route("/hotspot/api/sites/", sites_endpoint, methods=["GET", "POST"]),
    Route("/hotspot/api/sites", sites_endpoint, methods=["GET", "POST"]),
    Route("/hotspot/api/sites/{site_id}/", site_delete_endpoint, methods=["DELETE"]),
    Route("/hotspot/api/sites/{site_id}", site_delete_endpoint, methods=["DELETE"]),
    Route("/hotspot/api/sessions/{session_id}/", session_detail),
    Route("/hotspot/api/sessions/{session_id}/recording/", session_recording),
    Route("/hotspot/api/events/", events_feed),
    Route("/mcp", mcp_endpoint, methods=["GET", "POST", "DELETE", "OPTIONS"]),
    Route("/mcp/", mcp_endpoint, methods=["GET", "POST", "DELETE", "OPTIONS"]),
    Route("/mcp/sse", mcp_sse_endpoint, methods=["GET", "OPTIONS"]),
    Route("/mcp/sse/", mcp_sse_endpoint, methods=["GET", "OPTIONS"]),
    Route("/mcp/messages/", mcp_messages_endpoint, methods=["POST", "OPTIONS"]),
    Route("/mcp/messages", mcp_messages_endpoint, methods=["POST", "OPTIONS"]),
]

app = Starlette(routes=routes)


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("HOTSPOT_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT") or os.environ.get("HOTSPOT_PORT") or "8000")
    uvicorn.run(app, host=host, port=port, log_level="info")
