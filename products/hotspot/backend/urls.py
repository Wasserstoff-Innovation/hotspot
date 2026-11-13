"""Hotspot routes. Pages sit at the site root; the API lives under /hotspot/."""

from django.urls import path, re_path
from django.views.generic.base import RedirectView

from products.hotspot.backend import views

SESSION_ID = r"(?P<session_id>[A-Za-z0-9_.:@-]{1,200})"

urlpatterns = [
    path("", views.landing, name="hotspot_landing"),
    path("demo/", views.demo, name="hotspot_demo"),
    # The sample site switches views with pushState, so a reload on a sub-path must render it too.
    re_path(r"^demo/(?P<view>[a-z0-9-]{1,40})/?$", views.demo, name="hotspot_demo_view"),
    path("dashboard/", views.dashboard, name="hotspot_dashboard"),
    path("dashboard/login/", views.login, name="hotspot_login"),
    path("dashboard/logout/", views.logout, name="hotspot_logout"),
    path("app/", RedirectView.as_view(url="/dashboard/", permanent=False)),
    path("hotspot.js", views.sdk, name="hotspot_sdk"),
    re_path(r"^hotspot/assets/(?P<name>[a-z]+\.(?:js|css|json))$", views.asset, name="hotspot_asset"),
    path("llms.txt", views.llms_txt, name="hotspot_llms_txt"),
    path("sitemap.xml", views.sitemap_xml, name="hotspot_sitemap"),
    path("robots.txt", views.robots_txt, name="hotspot_robots"),
    re_path(r"^docs(?:/(?P<slug>[a-z0-9_-]+))?/?$", views.docs, name="hotspot_docs"),
    re_path(r"^(?:SKILL|skill)\.md$", views.skill_md, name="hotspot_skill_md"),
    path("hotspot/skill.md", views.skill_md, name="hotspot_prefixed_skill_md"),
    re_path(r"^(?:dashboard/)?connect/?$", views.connect_page, name="hotspot_connect"),
    re_path(r"^hotspot/api/connect/start/?$", views.connect_start_endpoint, name="hotspot_api_connect_start"),
    re_path(r"^hotspot/api/connect/status/?$", views.connect_status_endpoint, name="hotspot_api_connect_status"),
    re_path(r"^hotspot/api/connect/approve/?$", views.connect_approve_endpoint, name="hotspot_api_connect_approve"),
    re_path(r"^hotspot/api/connect/token/?$", views.connect_token_endpoint, name="hotspot_api_connect_token"),
    path("favicon.ico", views.favicon, name="hotspot_favicon"),
    re_path(r"^hotspot/batch/?$", views.capture, name="hotspot_capture"),
    re_path(r"^hotspot/replay/?$", views.capture_recording, name="hotspot_capture_recording"),
    re_path(r"^hotspot/api/overview/?$", views.overview, name="hotspot_api_overview"),
    re_path(r"^hotspot/api/sites/?$", views.sites_endpoint, name="hotspot_api_sites"),
    re_path(
        r"^hotspot/api/sites/(?P<site_id>[a-z0-9_-]{1,100})/?$",
        views.site_delete_endpoint,
        name="hotspot_api_site_delete",
    ),
    re_path(rf"^hotspot/api/sessions/{SESSION_ID}/?$", views.session_detail, name="hotspot_api_session"),
    re_path(rf"^hotspot/api/sessions/{SESSION_ID}/recording/?$", views.session_recording, name="hotspot_api_recording"),
    re_path(r"^hotspot/api/events/?$", views.events_feed, name="hotspot_api_events"),
    re_path(r"^mcp/?$", views.mcp_endpoint, name="hotspot_mcp"),
    re_path(r"^hotspot/mcp/?$", views.mcp_endpoint, name="hotspot_prefixed_mcp"),
]
