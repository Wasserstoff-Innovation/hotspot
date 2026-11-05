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
    path("llms.txt", views.llms_txt, name="hotspot_llms_txt"),
    path("favicon.ico", views.favicon, name="hotspot_favicon"),
    re_path(r"^hotspot/batch/?$", views.capture, name="hotspot_capture"),
    re_path(r"^hotspot/api/overview/?$", views.overview, name="hotspot_api_overview"),
    re_path(rf"^hotspot/api/sessions/{SESSION_ID}/?$", views.session_detail, name="hotspot_api_session"),
    re_path(r"^hotspot/api/events/?$", views.events_feed, name="hotspot_api_events"),
]
