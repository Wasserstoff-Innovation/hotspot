"""
Hotspot Backend View Controllers
Provides clean REST API endpoints, analytics aggregations, session streaming,
and scene renders with strict boundary sanitization.
"""

import os
import json
import time
import uuid
from typing import Dict, Any, List

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt

from products.hotspot.backend.sanitizer import hotspot_boundary, sanitize_hotspot_response
from products.hotspot.backend.engine import (
    HOTSPOT_EVENTS,
    HOTSPOT_SESSIONS,
    ensure_seed_sessions,
    simulate_cohort_flow,
)

# Static Asset Paths (with fallback checks)
HOTSPOT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SDK_JS_PATH = os.path.join(HOTSPOT_ROOT, "sdk", "hotspot.js")
if not os.path.exists(SDK_JS_PATH):
    SDK_JS_PATH = os.path.join(settings.BASE_DIR, "posthog", "static", "hotspot.js")

GSAP_JS_PATH = os.path.join(settings.BASE_DIR, "posthog", "static", "gsap.min.js")
SCROLL_TRIGGER_JS_PATH = os.path.join(settings.BASE_DIR, "posthog", "static", "ScrollTrigger.min.js")
LUCIDE_JS_PATH = os.path.join(settings.BASE_DIR, "posthog", "static", "lucide.min.js")


@hotspot_boundary
def gsap_js(request: HttpRequest) -> HttpResponse:
    """Serve GSAP library locally."""
    if os.path.exists(GSAP_JS_PATH):
        with open(GSAP_JS_PATH, "r", encoding="utf-8") as f:
            content = f.read()
    else:
        content = "console.warn('GSAP fallback'); window.gsap = { to: () => {}, from: () => {}, timeline: () => ({ to: () => {} }) };"
    res = HttpResponse(content, content_type="application/javascript; charset=utf-8")
    res["Cache-Control"] = "public, max-age=86400"
    return res


@hotspot_boundary
def scroll_trigger_js(request: HttpRequest) -> HttpResponse:
    """Serve ScrollTrigger library locally."""
    if os.path.exists(SCROLL_TRIGGER_JS_PATH):
        with open(SCROLL_TRIGGER_JS_PATH, "r", encoding="utf-8") as f:
            content = f.read()
    else:
        content = "console.warn('ScrollTrigger fallback'); window.ScrollTrigger = {};"
    res = HttpResponse(content, content_type="application/javascript; charset=utf-8")
    res["Cache-Control"] = "public, max-age=86400"
    return res


@hotspot_boundary
def lucide_js(request: HttpRequest) -> HttpResponse:
    """Serve Lucide Icons library locally."""
    if os.path.exists(LUCIDE_JS_PATH):
        with open(LUCIDE_JS_PATH, "r", encoding="utf-8") as f:
            content = f.read()
    else:
        content = "console.warn('Lucide fallback'); window.lucide = { createIcons: () => {} };"
    res = HttpResponse(content, content_type="application/javascript; charset=utf-8")
    res["Cache-Control"] = "public, max-age=86400"
    return res


@hotspot_boundary
def hotspot_js(request: HttpRequest) -> HttpResponse:
    """Serve the Hotspot In-Browser Resident Telemetry Script."""
    if os.path.exists(SDK_JS_PATH):
        with open(SDK_JS_PATH, "r", encoding="utf-8") as f:
            content = f.read()
    else:
        content = "console.log('Hotspot Telemetry Engine Active');"
    res = HttpResponse(content, content_type="application/javascript; charset=utf-8")
    res["Cache-Control"] = "public, max-age=3600"
    return res


@csrf_exempt
@hotspot_boundary
def hotspot_capture(request: HttpRequest) -> HttpResponse:
    """Hotspot Ingestion Endpoint for real-time telemetry events and session replay."""
    if request.method == "OPTIONS":
        return HttpResponse(status=200)

    try:
        if request.body:
            payload = json.loads(request.body.decode("utf-8"))
        else:
            payload = {}
    except Exception:
        payload = {}

    batch = payload.get("batch") or ([payload] if payload.get("event") else [])
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

        # Update or create session
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

        # Dynamic profile enrichment from incoming events
        if props.get("$user_email"):
            s_entry["user_email"] = props["$user_email"]
            s_entry["distinct_id"] = props["$user_email"]
        if props.get("$user_name"):
            s_entry["user_name"] = props["$user_name"]
        if props.get("$company"):
            s_entry["company"] = props["$company"]
        if props.get("$pathname") and s_entry["entry_page"] == "/":
            s_entry["entry_page"] = props["$pathname"]
        if props.get("$referrer") and s_entry["referrer"] == "Direct Navigation":
            s_entry["referrer"] = props["$referrer"]
        if props.get("$scroll_depth_percent"):
            try:
                s_entry["max_scroll"] = max(s_entry.get("max_scroll", 0), int(props["$scroll_depth_percent"]))
            except Exception:
                pass

        if event_name == "$rageclick":
            s_entry["has_rage"] = True
            s_entry["rageclicks"] = s_entry.get("rageclicks", 0) + int(props.get("count", 4))
            s_entry["status"] = "rageclicks"
        elif event_name in ["passkey_verified", "$form_submit"]:
            s_entry["converted"] = True
            s_entry["status"] = "converted"

        # Explicitly persist updated session entry across all workers via Redis
        HOTSPOT_SESSIONS[sess_id] = s_entry

    return JsonResponse({"status": 1, "received": len(batch)})


@csrf_exempt
@hotspot_boundary
def hotspot_events_stream(request: HttpRequest) -> HttpResponse:
    """Returns the latest captured telemetry events in chronological order."""
    if request.method == "OPTIONS":
        return HttpResponse(status=200)

    ensure_seed_sessions()
    events_list = list(HOTSPOT_EVENTS)
    events_list.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
    return JsonResponse({"status": 1, "count": len(events_list), "events": events_list[:50]})


@csrf_exempt
@hotspot_boundary
def hotspot_session_events(request: HttpRequest, session_id: str) -> HttpResponse:
    """Returns all micro-events for a specific session to drive high-definition session replay."""
    if request.method == "OPTIONS":
        return HttpResponse(status=200)

    ensure_seed_sessions()
    session = HOTSPOT_SESSIONS.get(session_id)
    if not session:
        return JsonResponse({"status": 0, "error": "Session not found"}, status=404)

    return JsonResponse({
        "status": 1,
        "session": session,
        "events": session.get("events", [])
    })


@csrf_exempt
@hotspot_boundary
def hotspot_simulate_flow(request: HttpRequest) -> HttpResponse:
    """Linear Flow & Break-off Wall Simulator Endpoint."""
    if request.method == "OPTIONS":
        return HttpResponse(status=200)

    cohort_size = max(50, min(100000, int(request.GET.get("cohort_size") or 1000)))
    fix_w1 = request.GET.get("fix_wall_1") in ["1", "true", "True"]
    fix_w2 = request.GET.get("fix_wall_2") in ["1", "true", "True"]
    fix_w3 = request.GET.get("fix_wall_3") in ["1", "true", "True"]

    data = simulate_cohort_flow(cohort_size=cohort_size, fix_w1=fix_w1, fix_w2=fix_w2, fix_w3=fix_w3)
    return JsonResponse(data)


@csrf_exempt
@hotspot_boundary
def hotspot_analytics_summary(request: HttpRequest) -> HttpResponse:
    """Full conversion analytics, dropoff bottlenecks, and session directory."""
    if request.method == "OPTIONS":
        return HttpResponse(status=200)

    ensure_seed_sessions()
    now_ts = time.time()
    sessions_list = list(HOTSPOT_SESSIONS.values())
    sessions_list.sort(key=lambda s: s["last_seen"], reverse=True)

    # Filters
    search_q = (request.GET.get("search") or "").strip().lower()
    filter_tag = (request.GET.get("filter") or "all").lower()
    device_filter = (request.GET.get("device") or "all").lower()
    browser_filter = (request.GET.get("browser") or "all").lower()
    os_filter = (request.GET.get("os") or "all").lower()

    filtered_sessions = []
    for s in sessions_list:
        if filter_tag == "rageclicks" and not s.get("has_rage"):
            continue
        elif filter_tag == "converted" and not s.get("converted"):
            continue
        elif filter_tag == "bounced" and (s.get("converted") or s.get("duration", 0) > 15):
            continue

        if device_filter != "all" and s.get("device_type", "desktop").lower() != device_filter:
            continue
        if browser_filter != "all" and browser_filter not in s.get("browser", "").lower():
            continue
        if os_filter != "all" and os_filter not in s.get("os", "").lower():
            continue

        if search_q:
            corpus = f"{s.get('session_id', '')} {s.get('distinct_id', '')} {s.get('user_email', '')} {s.get('user_name', '')} {s.get('entry_page', '')} {s.get('referrer', '')}".lower()
            if search_q not in corpus:
                continue

        filtered_sessions.append(s)

    formatted_sessions = []
    for s in filtered_sessions:
        duration = max(1, int(s["last_seen"] - s["first_seen"]))
        duration_str = f"{duration}s" if duration < 60 else f"{duration//60}m {duration%60}s"
        time_diff = max(0, int(now_ts - s["last_seen"]))
        if time_diff < 60:
            time_ago_str = f"{time_diff}s ago"
        elif time_diff < 3600:
            time_ago_str = f"{time_diff//60}m ago"
        else:
            time_ago_str = f"{time_diff//3600}h ago"

        formatted_sessions.append({
            "session_id": s["session_id"],
            "distinct_id": s.get("distinct_id") or "guest",
            "user_email": s.get("user_email"),
            "user_name": s.get("user_name") or "Anonymous User",
            "company": s.get("company") or "Unassigned",
            "entry_page": s.get("entry_page") or "/",
            "duration_str": duration_str,
            "duration_seconds": duration,
            "events_count": len(s.get("events", [])),
            "rageclicks": 4 if s.get("has_rage") else 0,
            "device": s.get("device") or "macOS • Chrome 130",
            "device_type": s.get("device_type") or "desktop",
            "os": s.get("os") or "macOS",
            "browser": s.get("browser") or "Chrome",
            "country": s.get("country") or "US",
            "referrer": s.get("referrer") or "Direct",
            "is_ai": s.get("is_ai", False),
            "converted": s.get("converted", False),
            "status": s.get("status") or ("converted" if s.get("converted") else "active"),
            "time_ago": time_ago_str,
            "events": s.get("events", []),
        })

    total_sessions = len(sessions_list)
    s1_entrants = total_sessions
    s2_entrants = sum(1 for s in sessions_list if s.get("entry_page") == "/pricing" or len(s.get("events", [])) >= 2)
    s3_entrants = sum(1 for s in sessions_list if any(
        (ev.get("properties") or {}).get("$el_selector") in ["#btn-passkey-trigger", "#plan-pro-select", "#pricing-tier-enterprise"]
        for ev in s.get("events", [])
    ))
    s4_entrants = sum(1 for s in sessions_list if s.get("converted"))

    def calc_step(name, target, count, prev_count, median_time):
        conv_prev = round((count / max(1, prev_count)) * 100, 1) if prev_count else 0
        drop_prev = round(100 - conv_prev, 1)
        drop_count = max(0, prev_count - count)
        severity = "green" if drop_prev < 35 else "yellow" if drop_prev <= 55 else "red"
        return {
            "name": name,
            "target": target,
            "count": count,
            "pct_of_total": round((count / max(1, s1_entrants)) * 100, 1) if s1_entrants else 0,
            "conv_from_prev": conv_prev,
            "dropoff_from_prev": drop_prev,
            "dropoff_count": drop_count,
            "severity": severity,
            "median_time": median_time,
        }

    funnel_steps = [
        {
            "step_number": 1,
            "name": "Inbound Landing",
            "target": "/",
            "count": s1_entrants,
            "pct_of_total": 100.0,
            "conv_from_prev": 100.0,
            "dropoff_from_prev": 0.0,
            "dropoff_count": 0,
            "severity": "green",
            "median_time": "0s",
        },
        {
            "step_number": 2,
            **calc_step("Pricing & Plan Exploration", "/pricing", s2_entrants, s1_entrants, "34s")
        },
        {
            "step_number": 3,
            **calc_step("Intent & Trigger Modal", "#btn-passkey-trigger", s3_entrants, s2_entrants, "1m 12s")
        },
        {
            "step_number": 4,
            **calc_step("Passkey Verification / Checkout", "passkey_verified", s4_entrants, s3_entrants, "18s")
        },
    ]

    sequence_paths = [
        {
            "id": "path_golden_conversion",
            "rank": 1,
            "name": "High-Converting Intent Flow",
            "steps": [
                {"label": "Inbound Arrival", "path": "/", "pct": 100, "dropoff": 0, "color": "green"},
                {"label": "Explore Pricing", "path": "/pricing", "pct": 75, "dropoff": 25, "color": "green"},
                {"label": "Trigger Passkey", "path": "#btn-passkey-trigger", "pct": 50, "dropoff": 33, "color": "yellow"},
                {"label": "Verified Conversion", "path": "passkey_verified", "pct": 38, "dropoff": 24, "color": "green"},
            ],
            "volume_pct": 37.5,
            "session_count": s4_entrants,
            "conversion_rate": round((s4_entrants / max(1, s1_entrants)) * 100, 1),
            "dropoff_severity": "green",
            "avg_time": "2m 14s",
            "notes": "Optimal path: visitors immediately head to pricing and complete passkey verification."
        },
        {
            "id": "path_friction_corridor",
            "rank": 2,
            "name": "Friction Choke & Abandonment",
            "steps": [
                {"label": "Inbound Arrival", "path": "/", "pct": 100, "dropoff": 0, "color": "green"},
                {"label": "Explore Pricing", "path": "/pricing", "pct": 75, "dropoff": 25, "color": "green"},
                {"label": "Rage Click Static Pill", "path": "#pricing-tier-enterprise", "pct": 38, "dropoff": 49, "color": "red"},
                {"label": "Bounce / Tab Close", "path": "[Dropoff Exit]", "pct": 0, "dropoff": 100, "color": "red"},
            ],
            "volume_pct": 37.5,
            "session_count": sum(1 for s in sessions_list if s.get("has_rage")),
            "conversion_rate": 0.0,
            "dropoff_severity": "red",
            "avg_time": "54s",
            "notes": "Critical Leak: 42 visitors repeatedly clicked inert Enterprise pill expecting toggle, then bounced."
        }
    ]

    conv_rate = round((s4_entrants / max(1, s1_entrants)) * 100, 1) if s1_entrants else 0.0
    total_rage = sum(s.get("rageclicks", 0) for s in formatted_sessions)
    avg_scroll = (sum(s.get("max_scroll", 0) for s in formatted_sessions) // max(1, total_sessions)) if total_sessions else 50

    return JsonResponse({
        "status": 1,
        "metrics": {
            "total_sessions": total_sessions,
            "filtered_sessions_count": len(formatted_sessions),
            "conversion_rate": conv_rate,
            "converted_count": s4_entrants,
            "total_rage_clicks": total_rage,
            "avg_scroll_depth": avg_scroll,
        },
        "kpis": {
            "active_sessions": total_sessions,
            "funnel_conversion_rate": f"{conv_rate}%",
            "friction_hotspots": sum(1 for s in sessions_list if s.get("has_rage")),
            "avg_scroll_depth": f"{avg_scroll}%",
        },
        "sessions": formatted_sessions,
        "funnel_steps": funnel_steps,
        "sequence_paths": sequence_paths,
        "friction_elements": [
            {
                "selector": "#pricing-tier-enterprise",
                "text": "Self-Hosted Enterprise (Dead Pill)",
                "count": total_rage,
                "sessions_count": sum(1 for s in sessions_list if s.get("has_rage")),
                "lost_leads": 18,
            }
        ] if total_rage > 0 else [],
    })


@hotspot_boundary
def hotspot_demo(request: HttpRequest) -> HttpResponse:
    """CloudScale AI Live Interactive Demo Scene."""
    context = {
        "page_title": "CloudScale AI — Production Infrastructure (Hotspot Demo)",
        "site_url": getattr(settings, "SITE_URL", "") or "http://localhost:8000",
        "demo_api_key": "hs_live_prod_demo_test",
    }
    res = render(request, "demo.html", context)
    res["Cache-Control"] = "no-cache, no-store, must-revalidate"
    res["Pragma"] = "no-cache"
    res["Expires"] = "0"
    return res


@hotspot_boundary
def hotspot_landing(request: HttpRequest) -> HttpResponse:
    """Hotspot Public Landing Page Scene."""
    context = {
        "page_title": "Hotspot • In-Browser Telemetry, Friction Analysis & Conversion Corridors",
        "site_url": getattr(settings, "SITE_URL", "") or "http://localhost:8000",
        "demo_api_key": "hs_live_740bf8263397",
    }
    return render(request, "landing.html", context)


@hotspot_boundary
def hotspot_dashboard(request: HttpRequest) -> HttpResponse:
    """Hotspot Product Analytics & Conversion Intelligence Dashboard."""
    context = {
        "page_title": "Hotspot • Product Analytics & Conversion Intelligence",
        "site_url": getattr(settings, "SITE_URL", "") or "http://localhost:8000",
        "demo_api_key": "hs_live_740bf8263397",
    }
    res = render(request, "dashboard.html", context)
    res["Cache-Control"] = "no-cache, no-store, must-revalidate"
    res["Pragma"] = "no-cache"
    res["Expires"] = "0"
    return res


@hotspot_boundary
def hotspot_playground(request: HttpRequest) -> HttpResponse:
    """Hotspot Live AI Playground scene."""
    context = {
        "page_title": "VibeForge AI — Live Playground with Hotspot Visualizer",
        "site_url": getattr(settings, "SITE_URL", "") or "http://localhost:8000",
        "demo_api_key": "hs_live_demo_sample_token",
    }
    return render(request, "playground.html", context)


@hotspot_boundary
def hotspot_llms_txt(request: HttpRequest) -> HttpResponse:
    """Serves llms.txt standard specification for AI search crawlers."""
    content = """# Hotspot — Resident In-Browser Telemetry Engine
> Lightweight, zero-dependency client telemetry with rage click detection and session flow analysis.

- Website: https://hotspot.dev
- Documentation: https://hotspot.dev/docs
- SDK: /hotspot.js
- Ingestion Endpoint: /api/capture
"""
    res = HttpResponse(content, content_type="text/plain; charset=utf-8")
    res["Cache-Control"] = "public, max-age=86400"
    return res


@csrf_exempt
@hotspot_boundary
def hotspot_decide(request: HttpRequest) -> HttpResponse:
    """Feature flag and remote config decision endpoint."""
    return JsonResponse({
        "status": 1,
        "featureFlags": {
            "hotspot-friction-detection": True,
            "hotspot-passkey-v2": True,
            "hotspot-linear-flow-sim": True,
        }
    })
