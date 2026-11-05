from __future__ import annotations

import pytest

import fakeredis

from products.hotspot.backend.store import MemoryBackend, RedisBackend, SessionStore
from products.hotspot.backend.test.helpers import capture_events, event_item, make_config, visit

NOW = 1_700_000_000.0


def _stores() -> list[tuple[str, SessionStore]]:
    config = make_config(HOTSPOT_MAX_EVENTS="6", HOTSPOT_MAX_SESSION_EVENTS="4")
    return [
        ("memory", SessionStore(MemoryBackend(config), config)),
        ("redis", SessionStore(RedisBackend(fakeredis.FakeRedis(), config), config)),
    ]


@pytest.mark.parametrize(("name", "store"), _stores(), ids=lambda value: value if isinstance(value, str) else "")
def test_events_fold_into_one_session_summary(name: str, store: SessionStore) -> None:
    config = make_config()
    items = [
        event_item("$pageview", "sess-a", NOW, **{"$pathname": "/", "$viewport_width": 0, "$viewport_height": 0}),
        event_item(
            "$pageview",
            "sess-a",
            NOW + 10,
            **{"$pathname": "/pricing", "$viewport_width": 1440, "$viewport_height": 900},
        ),
        event_item("$autocapture", "sess-a", NOW + 20, **{"$el_selector": "#buy", "$is_dead_click": True}),
        event_item("$rageclick", "sess-a", NOW + 21, **{"$el_selector": "#buy"}),
        event_item("$pageleave", "sess-a", NOW + 30, **{"$scroll_depth_percent": 72, "$duration_seconds": 30}),
        event_item("$identify", "sess-a", NOW + 31, **{"$identified_id": "u-9", "$set": {"email": "a@example.com"}}),
        event_item("$form_submit", "sess-a", NOW + 40),
    ]
    store.record(capture_events(config, items[:3], NOW), now=NOW)
    store.record(capture_events(config, items[3:], NOW), now=NOW)

    (summary,) = store.sessions()
    assert summary["pages"] == ["/", "/pricing"]
    assert (summary["entry_page"], summary["exit_page"]) == ("/", "/pricing")
    assert (summary["pageview_count"], summary["click_count"], summary["dead_click_count"]) == (2, 1, 1)
    assert (summary["rage_click_count"], summary["form_submit_count"], summary["event_count"]) == (1, 1, 7)
    assert (summary["max_scroll_depth"], summary["active_seconds"], summary["duration_seconds"]) == (72, 30, 40)
    assert summary["identity"] == {"email": "a@example.com"} and summary["distinct_id"] == "u-9"
    assert (summary["converted"], summary["conversion_event"]) == (True, "$form_submit")
    assert summary["browser"] == "Chrome" and summary["device_type"] == "desktop"
    # The first page view came from an unrendered tab with a 0 x 0 viewport; the next one fills it in.
    assert (summary["viewport_width"], summary["viewport_height"]) == (1440, 900)

    detail = store.session("sess-a")
    assert detail is not None
    # Per-session history is capped at the configured size and keeps the newest events.
    assert [event["event"] for event in detail["events"]] == [
        "$autocapture",
        "$rageclick",
        "$pageleave",
        "$identify",
        "$form_submit",
    ][-4:]


@pytest.mark.parametrize(("name", "store"), _stores(), ids=lambda value: value if isinstance(value, str) else "")
def test_sessions_are_listed_newest_first_and_the_feed_is_capped(name: str, store: SessionStore) -> None:
    config = make_config()
    store.record(capture_events(config, visit("old", NOW - 100, ["/a"]), NOW), now=NOW)
    store.record(capture_events(config, visit("new", NOW, ["/b", "/c", "/d"]), NOW), now=NOW)
    store.record(capture_events(config, visit("mid", NOW - 50, ["/e", "/f", "/g"]), NOW), now=NOW)

    assert [summary["session_id"] for summary in store.sessions()] == ["new", "mid", "old"]
    feed = store.recent_events(limit=100)
    assert len(feed) == 6
    assert [event["properties"]["$pathname"] for event in feed[:3]] == ["/g", "/f", "/e"]
    assert [event["session_id"] for event in store.recent_events(limit=10, since=NOW - 60)][:1] == ["mid"]
