import json
import base64
import os
from typing import Any
from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse, HttpResponseRedirect
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import render
import structlog

logger = structlog.get_logger(__name__)

# Path to static hotspot.js SDK and animations
HOTSPOT_JS_PATH = os.path.join(settings.BASE_DIR, "posthog", "static", "hotspot.js")
GSAP_JS_PATH = os.path.join(settings.BASE_DIR, "posthog", "static", "gsap.min.js")
SCROLL_TRIGGER_JS_PATH = os.path.join(settings.BASE_DIR, "posthog", "static", "ScrollTrigger.min.js")


def _add_cors_headers(response: HttpResponse) -> HttpResponse:
    response["Access-Control-Allow-Origin"] = "*"
    response["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, HEAD"
    response["Access-Control-Allow-Headers"] = (
        "Content-Type, Authorization, X-Requested-With, X-Hotspot-Key, User-Agent, Accept"
    )
    response["Access-Control-Allow-Credentials"] = "true"
    return response


@csrf_exempt
def gsap_js(request: HttpRequest) -> HttpResponse:
    """Serves local GSAP animation library with CORS headers."""
    if request.method == "OPTIONS":
        return _add_cors_headers(HttpResponse(status=200))
    try:
        with open(GSAP_JS_PATH, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        content = "// GSAP unavailable"
    response = HttpResponse(content, content_type="application/javascript; charset=utf-8")
    response["Cache-Control"] = "public, max-age=86400"
    return _add_cors_headers(response)


@csrf_exempt
def scroll_trigger_js(request: HttpRequest) -> HttpResponse:
    """Serves local ScrollTrigger library with CORS headers."""
    if request.method == "OPTIONS":
        return _add_cors_headers(HttpResponse(status=200))
    try:
        with open(SCROLL_TRIGGER_JS_PATH, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        content = "// ScrollTrigger unavailable"
    response = HttpResponse(content, content_type="application/javascript; charset=utf-8")
    response["Cache-Control"] = "public, max-age=86400"
    return _add_cors_headers(response)


@csrf_exempt
def hotspot_js(request: HttpRequest) -> HttpResponse:
    """Serves the standalone hotspot.js tracking SDK."""
    if request.method == "OPTIONS":
        return _add_cors_headers(HttpResponse(status=200))

    try:
        if os.path.exists(HOTSPOT_JS_PATH):
            with open(HOTSPOT_JS_PATH, "r", encoding="utf-8") as f:
                content = f.read()
        else:
            content = "// Hotspot JS placeholder\nconsole.log('Hotspot SDK loading...');"
    except Exception as e:
        logger.error("Failed to read hotspot.js", error=str(e))
        content = "// Hotspot JS error"

    response = HttpResponse(content, content_type="application/javascript; charset=utf-8")
    response["Cache-Control"] = "public, max-age=3600"
    return _add_cors_headers(response)


@csrf_exempt
def hotspot_capture(request: HttpRequest) -> HttpResponse:
    """High-performance capture endpoint for Hotspot events.

    Accepts single event or batch payloads from hotspot.js or SDKs.
    """
    if request.method == "OPTIONS":
        return _add_cors_headers(HttpResponse(status=200))

    data: dict[str, Any] = {}

    if request.method == "POST":
        content_type = request.content_type or ""
        body = request.body

        if "application/json" in content_type or (body and (body.startswith(b"{") or body.startswith(b"["))):
            try:
                data = json.loads(body.decode("utf-8"))
            except Exception:
                pass
        elif "application/x-www-form-urlencoded" in content_type:
            raw_data = request.POST.get("data")
            if raw_data:
                try:
                    # Support base64 encoded or direct JSON
                    if raw_data.startswith("{") or raw_data.startswith("["):
                        data = json.loads(raw_data)
                    else:
                        data = json.loads(base64.b64decode(raw_data).decode("utf-8"))
                except Exception:
                    pass
            else:
                data = dict(request.POST.items())

    elif request.method == "GET":
        raw_data = request.GET.get("data")
        if raw_data:
            try:
                if raw_data.startswith("{") or raw_data.startswith("["):
                    data = json.loads(raw_data)
                else:
                    data = json.loads(base64.b64decode(raw_data).decode("utf-8"))
            except Exception:
                pass

    api_key = data.get("api_key") or request.GET.get("api_key") or request.headers.get("X-Hotspot-Key")
    events = data.get("batch") if isinstance(data.get("batch"), list) else [data] if data.get("event") else []

    logger.info(
        "hotspot_events_received",
        event_count=len(events),
        api_key=api_key[:6] + "..." if api_key else "none",
        client_ip=request.META.get("REMOTE_ADDR"),
    )

    response = JsonResponse({"status": 1, "received": len(events)})
    return _add_cors_headers(response)


@csrf_exempt
def hotspot_decide(request: HttpRequest) -> HttpResponse:
    """Decide endpoint for feature flags and client configuration."""
    if request.method == "OPTIONS":
        return _add_cors_headers(HttpResponse(status=200))

    result = {
        "status": 1,
        "featureFlags": {},
        "sessionRecording": {
            "endpoint": "/s/",
        },
        "autocapture_exceptions": True,
        "capture_dead_clicks": True,
        "capture_performance": True,
    }
    response = JsonResponse(result)
    return _add_cors_headers(response)


def hotspot_landing(request: HttpRequest) -> HttpResponse:
    """Hotspot high-converting modern landing page."""
    context = {
        "page_title": "Hotspot • See Why Visitors Don't Convert",
        "site_url": settings.SITE_URL or "http://localhost:8000",
        "demo_api_key": "hs_live_demo_sample_token",
    }
    response = render(request, "landing.html", context)
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


def hotspot_dashboard(request: HttpRequest) -> HttpResponse:
    """Clean, layman-friendly, organized Hotspot audit & telemetry dashboard."""
    context = {
        "page_title": "Hotspot Dashboard • Conversion Health & Fix Queue",
        "site_url": settings.SITE_URL or "http://localhost:8000",
        "demo_api_key": "hs_live_demo_sample_token",
    }
    response = render(request, "dashboard.html", context)
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


def hotspot_playground(request: HttpRequest) -> HttpResponse:
    """Hotspot Live AI Playground scene."""
    context = {
        "page_title": "VibeForge AI — Live Playground with Hotspot Visualizer",
        "site_url": settings.SITE_URL or "http://localhost:8000",
        "demo_api_key": "hs_live_demo_sample_token",
    }
    response = render(request, "playground.html", context)
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response
