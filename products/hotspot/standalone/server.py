"""
Hotspot Standalone Server (Zero-PostHog Engine)
Runs Hotspot independently using Starlette / Uvicorn.
No monorepo dependencies, no 2GB Docker image, pure standalone operation.
"""

import os
import json
import time
from typing import Dict, Any, List

from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

# Resolve Directories
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
SDK_DIR = os.path.join(BASE_DIR, "sdk")

# Import Core Domain Logic
import sys
sys.path.insert(0, os.path.dirname(BASE_DIR))
from products.hotspot.backend.engine import (
    HOTSPOT_EVENTS,
    HOTSPOT_SESSIONS,
    ensure_seed_sessions,
    simulate_cohort_flow,
)

# Initialize seed sessions
ensure_seed_sessions()


def render_template(filename: str, context: Dict[str, Any] = None) -> str:
    path = os.path.join(TEMPLATES_DIR, filename)
    if not os.path.exists(path):
        return f"<h1>Template {filename} not found</h1>"
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()
    if context:
        for k, v in context.items():
            html = html.replace(f"{{{{ {k} }}}}", str(v))
    return html


# ─── HTTP Route Handlers ────────────────────────────────────────────────────────

async def landing_page(request):
    html = render_template("landing.html", {
        "page_title": "Hotspot • Developer Telemetry & Conversion Corridors",
        "site_url": "http://localhost:8000",
        "demo_api_key": "hs_live_740bf8263397",
    })
    res = HTMLResponse(html)
    res.headers["Server"] = "Hotspot/2.4"
    res.headers["X-Powered-By"] = "Hotspot Standalone Engine"
    return res


async def dashboard_page(request):
    html = render_template("dashboard.html", {
        "page_title": "Hotspot • Product Analytics & Conversion Intelligence",
        "site_url": "http://localhost:8000",
        "demo_api_key": "hs_live_740bf8263397",
    })
    res = HTMLResponse(html)
    res.headers["Server"] = "Hotspot/2.4"
    res.headers["X-Powered-By"] = "Hotspot Standalone Engine"
    res.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return res


async def serve_sdk(request):
    sdk_path = os.path.join(SDK_DIR, "hotspot.js")
    with open(sdk_path, "r", encoding="utf-8") as f:
        content = f.read()
    res = Response(content, media_type="application/javascript; charset=utf-8")
    res.headers["Server"] = "Hotspot/2.4"
    res.headers["Cache-Control"] = "public, max-age=3600"
    return res


async def capture_events(request):
    try:
        body = await request.json()
    except Exception:
        body = {}

    batch = body.get("batch") or ([body] if body.get("event") else [])
    now_ts = time.time()

    for item in batch:
        event_name = item.get("event") or "unknown"
        distinct_id = item.get("distinct_id") or "anonymous"
        props = item.get("properties") or {}
        sess_id = props.get("$session_id") or "hs_sess_live_" + str(int(now_ts))

        ev = {
            "event": event_name,
            "timestamp": now_ts,
            "distinct_id": distinct_id,
            "session_id": sess_id,
            "properties": props,
        }
        HOTSPOT_EVENTS.append(ev)

        if sess_id not in HOTSPOT_SESSIONS:
            HOTSPOT_SESSIONS[sess_id] = {
                "session_id": sess_id,
                "distinct_id": distinct_id,
                "user_email": props.get("$user_email") or (distinct_id if "@" in distinct_id else None),
                "user_name": props.get("$user_name") or ("User " + sess_id[-4:]),
                "company": props.get("$company") or "Live Visitor",
                "browser": props.get("$browser") or "Chrome",
                "os": props.get("$os") or "macOS",
                "device": f"{props.get('$os', 'macOS')} • {props.get('$browser', 'Chrome')}",
                "device_type": props.get("$device_type") or "desktop",
                "entry_page": props.get("$pathname") or "/",
                "referrer": props.get("$referrer") or "Direct Navigation",
                "is_ai": bool(props.get("$is_ai")),
                "duration": 1,
                "has_rage": False,
                "converted": False,
                "status": "active",
                "country": "US",
                "event_count": 0,
                "events": [],
                "first_seen": now_ts,
                "last_seen": now_ts,
            }

        s_entry = HOTSPOT_SESSIONS[sess_id]
        s_entry["events"].append(ev)
        s_entry["event_count"] = len(s_entry["events"])
        s_entry["last_seen"] = now_ts
        s_entry["duration"] = max(1, int(now_ts - s_entry["first_seen"]))

        if event_name == "$rageclick":
            s_entry["has_rage"] = True
            s_entry["status"] = "rageclicks"
        elif event_name in ["passkey_verified", "$form_submit"]:
            s_entry["converted"] = True
            s_entry["status"] = "converted"

    return JSONResponse({"status": 1, "received": len(batch)})


async def simulate_flow(request):
    params = request.query_params
    cohort_size = max(50, min(100000, int(params.get("cohort_size") or 1000)))
    fix_w1 = params.get("fix_wall_1") in ["1", "true", "True"]
    fix_w2 = params.get("fix_wall_2") in ["1", "true", "True"]
    fix_w3 = params.get("fix_wall_3") in ["1", "true", "True"]

    data = simulate_cohort_flow(cohort_size=cohort_size, fix_w1=fix_w1, fix_w2=fix_w2, fix_w3=fix_w3)
    return JSONResponse(data)


async def session_events(request):
    sess_id = request.path_params["session_id"]
    session = HOTSPOT_SESSIONS.get(sess_id)
    if not session:
        return JSONResponse({"status": 0, "error": "Session not found"}, status=404)
    return JSONResponse({"status": 1, "session": session, "events": session.get("events", [])})


routes = [
    Route("/", landing_page),
    Route("/landing/", landing_page),
    Route("/dashboard/", dashboard_page),
    Route("/hotspot.js", serve_sdk),
    Route("/api/capture", capture_events, methods=["POST", "OPTIONS"]),
    Route("/api/simulate_flow", simulate_flow),
    Route("/api/session/{session_id}", session_events),
]

middleware = [
    Middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]),
]

app = Starlette(debug=False, routes=routes, middleware=middleware)

if __name__ == "__main__":
    import uvicorn
    print("🚀 Starting Hotspot Standalone Server on http://0.0.0.0:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)
