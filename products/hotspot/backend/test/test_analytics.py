from __future__ import annotations

from products.hotspot.backend.analytics import OverviewQuery, build_overview, session_status
from products.hotspot.backend.store import MemoryBackend, SessionStore
from products.hotspot.backend.test.helpers import SAFARI_IPHONE, capture_events, event_item, make_config, visit

NOW = 1_700_000_000.0


def _populated_store() -> SessionStore:
    config = make_config()
    store = SessionStore(MemoryBackend(config), config)
    old = NOW - 3600
    # Converted: lands, clicks the buy button, submits the form.
    store.record(
        capture_events(
            config,
            [
                *visit("converted", old, ["/", "/pricing"]),
                event_item("$autocapture", "converted", old + 20, **{"$el_selector": "#buy", "$pathname": "/pricing"}),
                event_item("$form_submit", "converted", old + 30),
                # Two page leaves for the same page in one session: the tab was hidden and shown again.
                event_item(
                    "$pageleave",
                    "converted",
                    old + 31,
                    **{"$pathname": "/pricing", "$duration_seconds": 10, "$scroll_depth_percent": 40},
                ),
                event_item(
                    "$pageleave",
                    "converted",
                    old + 45,
                    **{"$pathname": "/pricing", "$duration_seconds": 5, "$scroll_depth_percent": 80},
                ),
                event_item(
                    "$web_vitals",
                    "converted",
                    old + 46,
                    **{"$pathname": "/pricing", "$lcp_ms": 1200, "$cls": 0.02, "$inp_ms": 80},
                ),
            ],
            NOW,
        ),
        now=NOW,
    )
    # Out of order: submits before clicking, so it must not count as a step-3 completion.
    store.record(
        capture_events(
            config,
            [
                *visit("reversed", old, ["/"]),
                event_item("$form_submit", "reversed", old + 5),
                event_item("$autocapture", "reversed", old + 9, **{"$el_selector": "#buy", "$pathname": "/"}),
            ],
            NOW,
        ),
        now=NOW,
    )
    # Rage clicker on mobile, bounces off /pricing.
    store.record(
        capture_events(
            config,
            [
                *visit("rager", old, ["/", "/pricing"], distinct_id="visitor-2"),
                event_item(
                    "$rageclick",
                    "rager",
                    old + 12,
                    "visitor-2",
                    **{"$el_selector": "#plan-enterprise", "$pathname": "/pricing", "$el_text": "Enterprise"},
                ),
                event_item(
                    "$rageclick",
                    "rager",
                    old + 13,
                    "visitor-2",
                    **{"$el_selector": "#plan-enterprise", "$pathname": "/pricing", "$el_text": "Enterprise"},
                ),
                event_item(
                    "$web_vitals",
                    "rager",
                    old + 14,
                    "visitor-2",
                    **{"$pathname": "/pricing", "$lcp_ms": 3600, "$cls": 0.31, "$inp_ms": 420, "$ttfb_ms": 900},
                ),
            ],
            NOW,
            user_agent=SAFARI_IPHONE,
        ),
        now=NOW,
    )
    # A single pageview an hour ago with nothing else: a bounce.
    store.record(capture_events(config, visit("bounce", old, ["/blog"], distinct_id="visitor-3"), NOW), now=NOW)
    return store


def test_overview_counts_only_what_really_happened() -> None:
    store = _populated_store()
    overview = build_overview(store, OverviewQuery(), make_config(), NOW)

    metrics = overview["metrics"]
    assert (metrics["sessions"], metrics["converted_sessions"], metrics["conversion_rate"]) == (4, 2, 50.0)
    assert (metrics["rage_clicks"], metrics["bounce_rate"]) == (2, 25.0)

    steps = overview["funnel"]["steps"]
    assert [step["label"] for step in steps] == ["Viewed a page", "Clicked anything", "Submitted a form"]
    assert [step["count"] for step in steps] == [4, 2, 1]
    assert steps[2]["median_seconds_to_next"] is None and steps[1]["median_seconds_to_next"] == 10.0

    assert overview["friction"]["rage_clicks"] == [
        {
            "pathname": "/pricing",
            "selector": "#plan-enterprise",
            "text": "Enterprise",
            "count": 2,
            "sessions": 1,
            "last_seen": NOW - 3600 + 13,
        }
    ]
    assert overview["paths"]["transitions"][0] == {"from": "/", "to": "/pricing", "count": 2, "share": 100.0}
    assert overview["paths"]["entry_pages"][0] == {"pathname": "/", "count": 3, "share": 75.0}
    assert overview["breakdowns"]["device_type"] == {"desktop": 3, "mobile": 1}
    pricing = next(row for row in overview["pages"] if row["pathname"] == "/pricing")
    assert (pricing["views"], pricing["sessions"], pricing["avg_seconds"], pricing["avg_scroll_depth"]) == (
        2,
        2,
        15.0,
        80,
    )
    # Web vitals are reported at the 75th percentile, so two samples report the slower one.
    assert pricing["vitals"] == {
        "lcp_ms": 3600,
        "cls": 0.31,
        "inp_ms": 420,
        "fcp_ms": None,
        "ttfb_ms": 900,
        "samples": 2,
    }
    assert (overview["web_vitals"]["lcp_ms"], overview["web_vitals"]["samples"]) == (3600, 2)
    assert next(row for row in overview["pages"] if row["pathname"] == "/")["vitals"] is None
    assert overview["setup"]["ingestion_keys_configured"] is False


def test_custom_funnel_steps_and_session_filters() -> None:
    store = _populated_store()
    query = OverviewQuery(funnel=("path:/pricing", "click:#plan-enterprise"), status="rage", device_type="mobile")
    overview = build_overview(store, query, make_config(), NOW)

    assert [step["count"] for step in overview["funnel"]["steps"]] == [1, 1]
    assert overview["funnel"]["steps"][0]["label"] == "Visited /pricing"
    assert [session["session_id"] for session in overview["sessions"]] == ["rager"]
    assert overview["sessions"][0]["status"] == "rage"
    assert overview["metrics"]["sessions"] == 1
    # Filter menus must keep every option even while one device is selected.
    assert overview["breakdowns"]["device_type"] == {"desktop": 3, "mobile": 1}
    assert (overview["scope"]["sessions_in_range"], overview["scope"]["sessions_stored"]) == (4, 4)

    searched = build_overview(store, OverviewQuery(search="/blog"), make_config(), NOW)
    assert [session["session_id"] for session in searched["sessions"]] == ["bounce"]
    assert searched["sessions"][0]["label"] == "Visitor bounce"


def test_status_prefers_conversion_then_rage_then_recency() -> None:
    base = {"last_seen": NOW - 3600, "pageview_count": 1, "click_count": 0, "duration_seconds": 0}
    assert session_status({**base, "converted": True, "rage_click_count": 3}, NOW) == "converted"
    assert session_status({**base, "rage_click_count": 1}, NOW) == "rage"
    assert session_status({**base, "last_seen": NOW - 10}, NOW) == "active"
    assert session_status(base, NOW) == "bounced"
    assert session_status({**base, "pageview_count": 3}, NOW) == "engaged"
