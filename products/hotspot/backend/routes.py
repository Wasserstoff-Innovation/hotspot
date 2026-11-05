"""
Hotspot URL Routing
Defines clean routes for landing, dashboard, analytics API, and script endpoints.
"""

from django.urls import path, re_path
from products.hotspot.backend import views

urlpatterns = [
    # Core Scenes
    path("", views.hotspot_landing, name="hotspot_landing"),
    path("landing/", views.hotspot_landing, name="hotspot_landing_alt"),
    path("demo/", views.hotspot_demo, name="hotspot_demo"),
    path("dashboard/", views.hotspot_dashboard, name="hotspot_dashboard"),
    path("app/", views.hotspot_dashboard, name="hotspot_app"),
    path("playground/", views.hotspot_playground, name="hotspot_playground"),
    path("llms.txt", views.hotspot_llms_txt, name="hotspot_llms_txt"),

    # Client Scripts
    path("hotspot.js", views.hotspot_js, name="hotspot_js"),
    path("static/hotspot.js", views.hotspot_js, name="hotspot_js_static"),
    path("gsap.js", views.gsap_js, name="hotspot_gsap_js"),
    path("ScrollTrigger.js", views.scroll_trigger_js, name="hotspot_scroll_trigger_js"),
    path("lucide.js", views.lucide_js, name="hotspot_lucide_js"),

    # REST APIs & Ingestion
    path("api/capture", views.hotspot_capture, name="hotspot_capture"),
    path("api/capture/", views.hotspot_capture, name="hotspot_capture_slash"),
    path("capture", views.hotspot_capture, name="hotspot_capture_root"),
    path("capture/", views.hotspot_capture, name="hotspot_capture_root_slash"),
    path("api/events", views.hotspot_events_stream, name="hotspot_events_stream"),
    path("api/analytics", views.hotspot_analytics_summary, name="hotspot_analytics_summary"),
    path("api/simulate_flow", views.hotspot_simulate_flow, name="hotspot_simulate_flow"),
    re_path(r"^api/session/(?P<session_id>[a-zA-Z0-9_\-]+)/?$", views.hotspot_session_events, name="hotspot_session_events"),
    path("decide", views.hotspot_decide, name="hotspot_decide"),
    path("decide/", views.hotspot_decide, name="hotspot_decide_slash"),
]
