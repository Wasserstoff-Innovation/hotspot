"""Hotspot standalone server: the same product without the PostHog stack.

Runs on Starlette and Uvicorn, stores sessions in Redis when REDIS_URL is set and in
memory otherwise. Every handler is a thin wrapper around products.hotspot.backend.service.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from products.hotspot.backend.auth import DASHBOARD_COOKIE, TOKEN_TTL_SECONDS  # noqa: E402
from products.hotspot.backend.security import (  # noqa: E402
    API_CSP,
    CAPTURE_CORS_HEADERS,
    NO_STORE_HEADERS,
    generate_nonce,
    is_same_site_form_post,
    page_csp,
)
from products.hotspot.backend.service import FAVICON_SVG, HotspotService, get_service  # noqa: E402
from products.hotspot.backend.templating import render_template  # noqa: E402

DASHBOARD_PATH = "/dashboard/"
LOGIN_PATH = "/dashboard/login/"


def _service() -> HotspotService:
    return get_service()


def _authorized(request: Request) -> bool:
    return _service().is_authorized(request.cookies.get(DASHBOARD_COOKIE), request.headers.get("authorization"))


def _page(request: Request, page: str, allow_page_frames: bool = False, status: int = 200, **extra: str) -> Response:
    nonce = generate_nonce()
    html = render_template(f"{page}.html", _service().page_context(page, nonce, **extra))
    response = HTMLResponse(html, status_code=status)
    response.headers["Content-Security-Policy"] = page_csp(nonce, allow_page_frames=allow_page_frames)
    return response


def _json(payload: dict[str, object], status: int = 200) -> Response:
    response = JSONResponse(payload, status_code=status)
    response.headers.update(NO_STORE_HEADERS)
    response.headers["Content-Security-Policy"] = API_CSP
    return response


async def landing(request: Request) -> Response:
    return _page(request, "landing")


async def demo(request: Request) -> Response:
    if not _service().config.demo_enabled:
        return PlainTextResponse("The sample site is disabled on this deployment.", status_code=404)
    return _page(request, "demo")


async def dashboard(request: Request) -> Response:
    if not _authorized(request):
        return RedirectResponse(LOGIN_PATH, status_code=302)
    response = _page(request, "dashboard", allow_page_frames=True)
    response.headers.update(NO_STORE_HEADERS)
    return response


async def app_redirect(request: Request) -> Response:
    return RedirectResponse(DASHBOARD_PATH, status_code=302)


async def login(request: Request) -> Response:
    service = _service()
    if not service.config.dashboard_protected or _authorized(request):
        return RedirectResponse(DASHBOARD_PATH, status_code=302)
    if request.method == "GET":
        return _page(request, "login", error="")
    if not is_same_site_form_post(request.headers):
        return PlainTextResponse("Cross-site form posts are refused.", status_code=403)
    form = await request.form()
    password = form.get("password")
    token = service.login(password if isinstance(password, str) else None)
    if token is None:
        return _page(request, "login", status=401, error="That password is not right. Try again.")
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


async def llms_txt(request: Request) -> Response:
    response = PlainTextResponse(_service().llms_txt())
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


async def favicon(request: Request) -> Response:
    response = Response(FAVICON_SVG, media_type="image/svg+xml")
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


async def capture(request: Request) -> Response:
    if request.method == "OPTIONS":
        response: Response = Response(status_code=204)
    else:
        status, payload = _service().capture(await request.body(), request.headers)
        response = JSONResponse(payload, status_code=status)
    response.headers.update(CAPTURE_CORS_HEADERS)
    response.headers["Content-Security-Policy"] = API_CSP
    return response


async def overview(request: Request) -> Response:
    if not _authorized(request):
        return _unauthorized()
    return _json(_service().overview(dict(request.query_params)))


async def session_detail(request: Request) -> Response:
    if not _authorized(request):
        return _unauthorized()
    detail = _service().session_detail(request.path_params["session_id"])
    if detail is None:
        return _json({"status": 0, "error": "Session not found"}, status=404)
    return _json(detail)


async def events_feed(request: Request) -> Response:
    if not _authorized(request):
        return _unauthorized()
    return _json(_service().events_feed(dict(request.query_params)))


def _unauthorized() -> Response:
    return _json({"status": 0, "error": "Sign in to the dashboard first"}, status=401)


routes = [
    Route("/", landing),
    Route("/demo/", demo),
    Route("/demo/{view}/", demo),
    Route("/demo/{view}", demo),
    Route("/dashboard/", dashboard),
    Route("/dashboard/login/", login, methods=["GET", "POST"]),
    Route("/dashboard/logout/", logout, methods=["POST"]),
    Route("/app/", app_redirect),
    Route("/hotspot.js", sdk),
    Route("/llms.txt", llms_txt),
    Route("/favicon.ico", favicon),
    Route("/hotspot/batch/", capture, methods=["POST", "OPTIONS"]),
    Route("/hotspot/batch", capture, methods=["POST", "OPTIONS"]),
    Route("/hotspot/api/overview/", overview),
    Route("/hotspot/api/sessions/{session_id}/", session_detail),
    Route("/hotspot/api/events/", events_feed),
]

app = Starlette(routes=routes)


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("HOTSPOT_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT") or os.environ.get("HOTSPOT_PORT") or "8000")
    uvicorn.run(app, host=host, port=port, log_level="info")
