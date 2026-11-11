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
    # Ninety minutes ago, so it sits in the hour before a "last hour" range.
    store.record(capture_events(config, visit("earlier", NOW - 5400, ["/docs"], distinct_id="visitor-4"), NOW), now=NOW)
    return store


def test_overview_counts_only_what_really_happened() -> None:
    store = _populated_store()
    overview = build_overview(store, OverviewQuery(), make_config(), NOW)

    metrics = overview["metrics"]
    assert (metrics["sessions"], metrics["converted_sessions"], metrics["conversion_rate"]) == (5, 2, 40.0)
    assert (metrics["rage_clicks"], metrics["bounce_rate"], metrics["visitors"]) == (2, 40.0, 4)
    assert (metrics["sessions_with_rage"], metrics["sessions_with_breakage"]) == (1, 1)
    assert overview["comparison"] is None

    steps = overview["funnel"]["steps"]
    assert [step["label"] for step in steps] == ["Viewed a page", "Clicked anything", "Submitted a form"]
    assert [step["count"] for step in steps] == [5, 2, 1]
    assert steps[2]["median_seconds_to_next"] is None and steps[1]["median_seconds_to_next"] == 10.0
    assert len(steps[1]["dropoff_sessions"]) == 3
    assert any(s["id"] == "top_journey" for s in overview["funnel"]["suggested_funnels"])

    flow = overview["paths"]["flow"]
    assert flow["steps"][0][0] == {"pathname": "/", "count": 3, "exits": 1, "converted": 2, "other": False}
    assert [node["pathname"] for node in flow["steps"][0]] == ["/", "/blog", "/docs"]
    assert flow["steps"][1] == [{"pathname": "/pricing", "count": 2, "exits": 2, "converted": 1, "other": False}]
    assert flow["links"] == [{"step": 0, "from": 0, "to": 0, "count": 2}] and flow["sessions"] == 5
    assert overview["referrers"]["channels"] == [
        {"channel": "direct", "sessions": 5, "share": 100.0, "converted": 2, "conversion_rate": 40.0}
    ]
    # Hourly buckets align to the clock, so the session from 90 minutes ago lands one bucket earlier.
    trend = overview["trend"]
    assert (trend["bucket_seconds"], trend["sessions"], sum(trend["pageviews"])) == (3600, [1, 4, 0], 7)

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
    assert overview["paths"]["entry_pages"][0] == {"pathname": "/", "count": 3, "share": 60.0}
    assert overview["breakdowns"]["device_type"] == {"desktop": 4, "mobile": 1}
    pricing = next(row for row in overview["pages"] if row["pathname"] == "/pricing")
    assert (pricing["views"], pricing["sessions"], pricing["avg_seconds"], pricing["avg_scroll_depth"]) == (
        2,
        2,
        15.0,
        80,
    )
    assert (pricing["converted"], pricing["conversion_rate"], pricing["rage_clicks"], pricing["errors"]) == (
        1,
        50.0,
        2,
        0,
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
    assert overview["breakdowns"]["device_type"] == {"desktop": 4, "mobile": 1}
    assert (overview["scope"]["sessions_in_range"], overview["scope"]["sessions_stored"]) == (5, 5)

    searched = build_overview(store, OverviewQuery(search="/blog"), make_config(), NOW)
    assert [session["session_id"] for session in searched["sessions"]] == ["bounce"]
    assert searched["sessions"][0]["label"] == "Visitor bounce"

    # The hour before the range is the comparison period: only the session from 90 minutes ago is in it.
    hourly = build_overview(store, OverviewQuery(hours=1), make_config(), NOW)
    assert (hourly["metrics"]["sessions"], hourly["comparison"]["sessions"], hourly["comparison"]["bounce_rate"]) == (
        4,
        1,
        100.0,
    )
    assert hourly["trend"]["bucket_seconds"] == 300 and sum(hourly["trend"]["sessions"]) == 4


def test_realtime_covers_the_last_30_minutes_with_countries_from_timezones() -> None:
    config = make_config()
    store = SessionStore(MemoryBackend(config), config)
    live = visit("live", NOW - 20, ["/", "/pricing"])
    recent = visit("recent", NOW - 1200, ["/blog"], distinct_id="visitor-2")
    stale = visit("stale", NOW - 7200, ["/old"], distinct_id="visitor-3")
    for item in live:
        item["properties"]["$timezone"] = "Asia/Calcutta"
    for item in recent:
        item["properties"]["$timezone"] = "Europe/Berlin"
    store.record(capture_events(config, [*live, *recent, *stale], NOW), now=NOW)

    realtime = build_overview(store, OverviewQuery(hours=1), make_config(), NOW)["realtime"]
    assert (realtime["active_now"], realtime["active_in_window"]) == (1, 2)
    assert realtime["countries"] == [{"country": "IN", "sessions": 1}, {"country": "DE", "sessions": 1}]
    assert realtime["pages"] == [{"pathname": "/pricing", "sessions": 1}, {"pathname": "/blog", "sessions": 1}]
    assert len(realtime["sessions_per_minute"]) == 30 and sum(realtime["sessions_per_minute"]) == 2


def test_status_prefers_conversion_then_rage_then_recency() -> None:
    base = {"last_seen": NOW - 3600, "pageview_count": 1, "click_count": 0, "duration_seconds": 0}
    assert session_status({**base, "converted": True, "rage_click_count": 3}, NOW) == "converted"
    assert session_status({**base, "rage_click_count": 1}, NOW) == "rage"
    assert session_status({**base, "last_seen": NOW - 10}, NOW) == "active"
    assert session_status(base, NOW) == "bounced"
    assert session_status({**base, "pageview_count": 3}, NOW) == "engaged"


def test_clarity_overview_metrics_users_quick_backs_smart_events_and_performance() -> None:
    config = make_config()
    store = SessionStore(MemoryBackend(config), config)

    # Returning visitor's earlier visit sits 48 hours ago (outside 24h range, still makes today's visit returning!).
    store.record(
        capture_events(
            config,
            visit("ret-old", NOW - 48 * 3600, ["/"], distinct_id="d-returning"),
            NOW,
        ),
        now=NOW,
    )
    # Today's returning visit: quick back (/ -> /pricing -> / within 6s), UTM campaign, good web vitals, custom event.
    store.record(
        capture_events(
            config,
            [
                event_item(
                    "$pageview",
                    "ret-new",
                    NOW - 600,
                    "d-returning",
                    **{
                        "$pathname": "/",
                        "$referrer": "https://www.google.com/search?q=hotspot",
                        "utm_campaign": "launch",
                        "utm_source": "google",
                        "utm_medium": "cpc",
                        "$timezone": "America/New_York",
                    },
                ),
                event_item("$pageview", "ret-new", NOW - 595, "d-returning", **{"$pathname": "/pricing"}),
                event_item("$pageview", "ret-new", NOW - 589, "d-returning", **{"$pathname": "/"}),
                event_item(
                    "$pageleave",
                    "ret-new",
                    NOW - 580,
                    "d-returning",
                    **{"$pathname": "/", "$duration_seconds": 18, "$scroll_depth_percent": 90},
                ),
                event_item("cta_click", "ret-new", NOW - 575, "d-returning", **{"$pathname": "/"}),
                event_item(
                    "$web_vitals",
                    "ret-new",
                    NOW - 570,
                    "d-returning",
                    **{"$pathname": "/", "$lcp_ms": 1100, "$inp_ms": 90, "$cls": 0.04},
                ),
            ],
            NOW,
        ),
        now=NOW,
    )
    # New visitor with poor web vitals & JS error.
    store.record(
        capture_events(
            config,
            [
                event_item(
                    "$pageview",
                    "new-poor",
                    NOW - 350,
                    "d-new",
                    **{"$pathname": "/docs", "$timezone": "Europe/Berlin"},
                ),
                event_item(
                    "$web_vitals",
                    "new-poor",
                    NOW - 340,
                    "d-new",
                    **{"$pathname": "/docs", "$lcp_ms": 4800, "$inp_ms": 600, "$cls": 0.35},
                ),
                event_item(
                    "$exception",
                    "new-poor",
                    NOW - 335,
                    "d-new",
                    **{"$pathname": "/docs", "$exception_message": "TypeError: boom"},
                ),
            ],
            NOW,
        ),
        now=NOW,
    )
    # Live AI-referred visitor (<5m ago) submitting a form.
    store.record(
        capture_events(
            config,
            [
                event_item(
                    "$pageview",
                    "ai-live",
                    NOW - 60,
                    "d-ai",
                    **{
                        "$pathname": "/signup",
                        "$referrer": "https://chatgpt.com/",
                        "$is_ai_referral": True,
                        "$ai_source": "ChatGPT",
                        "$timezone": "America/New_York",
                    },
                ),
                event_item("$form_submit", "ai-live", NOW - 40, "d-ai", **{"$pathname": "/signup"}),
            ],
            NOW,
        ),
        now=NOW,
    )

    overview = build_overview(store, OverviewQuery(hours=24), config, NOW)
    metrics = overview["metrics"]
    assert metrics["sessions"] == 3
    assert metrics["pages_per_session"] == 1.67
    assert metrics["avg_active_seconds"] == 6.0
    assert metrics["new_sessions"] == 2
    assert metrics["returning_sessions"] == 1
    assert metrics["quick_backs"] == 1
    assert metrics["sessions_with_quick_backs"] == 1

    users = overview["users_overview"]
    assert users["live_users"] == 1
    assert users["unique_users"] == 3
    assert (users["new_sessions"], users["returning_sessions"]) == (2, 1)
    assert (users["new_pct"], users["returning_pct"]) == (66.7, 33.3)
    assert users["top_visitor"]["sample_session_id"] == "ret-new"

    qb = overview["quick_backs"]
    assert (qb["count"], qb["sessions"], qb["share"]) == (1, 1, 33.3)
    assert qb["top_pairs"][0]["from"] == "/" and qb["top_pairs"][0]["to"] == "/pricing"

    events = {row["name"]: row for row in overview["smart_events"]}
    assert events["cta_click"]["kind"] == "custom" and events["cta_click"]["sessions"] == 1
    assert events["Form submitted"]["kind"] == "form" and events["Form submitted"]["sessions"] == 1

    perf = overview["performance"]
    assert perf["score"] == 50  # 3 good + 3 poor samples -> 50/100
    assert (perf["good_pct"], perf["poor_pct"]) == (50.0, 50.0)
    assert "new-poor" in perf["slow_session_ids"]

    campaigns = overview["referrers"]["campaigns"]
    assert campaigns == [
        {
            "campaign": "launch",
            "source": "google",
            "medium": "cpc",
            "sessions": 1,
            "share": 33.3,
            "converted": 0,
            "conversion_rate": 0.0,
        }
    ]

    # Global cross-widget scope filters & signal filters
    assert [
        s["session_id"] for s in build_overview(store, OverviewQuery(hours=24, channel="ai"), config, NOW)["sessions"]
    ] == ["ai-live"]
    assert {
        s["session_id"] for s in build_overview(store, OverviewQuery(hours=24, country="US"), config, NOW)["sessions"]
    } == {
        "ret-new",
        "ai-live",
    }
    assert [
        s["session_id"]
        for s in build_overview(store, OverviewQuery(hours=24, status="returning"), config, NOW)["sessions"]
    ] == ["ret-new"]
    assert [
        s["session_id"]
        for s in build_overview(store, OverviewQuery(hours=24, status="quick_back"), config, NOW)["sessions"]
    ] == ["ret-new"]
    assert [
        s["session_id"] for s in build_overview(store, OverviewQuery(hours=24, status="ai"), config, NOW)["sessions"]
    ] == ["ai-live"]
