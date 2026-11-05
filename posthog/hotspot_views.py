"""
Hotspot Views Compatibility Facade
Re-exports views directly from products.hotspot.backend.views
to decouple all Hotspot features into products/hotspot/.
"""

from products.hotspot.backend.views import (
    gsap_js,
    scroll_trigger_js,
    lucide_js,
    hotspot_js,
    hotspot_capture,
    hotspot_events_stream,
    hotspot_analytics_summary,
    hotspot_session_events,
    hotspot_simulate_flow,
    hotspot_decide,
    hotspot_landing,
    hotspot_demo,
    hotspot_dashboard,
    hotspot_playground,
    hotspot_llms_txt,
)

# Preserve simulate_visitor as an alias for backwards compatibility
hotspot_simulate_visitor = hotspot_simulate_flow
