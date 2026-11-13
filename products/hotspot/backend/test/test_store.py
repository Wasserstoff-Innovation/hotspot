from __future__ import annotations

import json
import zlib
import random
from typing import Any

import pytest

import fakeredis

from products.hotspot.backend.analytics import format_session
from products.hotspot.backend.config import HotspotConfig
from products.hotspot.backend.ingestion import RecordingChunk, parse_recording
from products.hotspot.backend.store import MemoryBackend, RedisBackend, SessionStore, compute_user_hash
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


def _recording_stores() -> list[tuple[str, SessionStore]]:
    config = make_config(HOTSPOT_MAX_RECORDING_BYTES="2048")
    return [
        ("memory", SessionStore(MemoryBackend(config), config)),
        ("redis", SessionStore(RedisBackend(fakeredis.FakeRedis(), config), config)),
    ]


def _chunk(config: HotspotConfig, session_id: str, events: list[dict[str, Any]]) -> RecordingChunk:
    body = json.dumps({"api_key": "hs_test", "session_id": session_id, "events": events}).encode()
    return parse_recording(body, None, config, NOW)


def _rrweb_event(kind: int, seconds: float, seed: int | None = None) -> dict[str, Any]:
    # Hex noise compresses badly, so a few chunks are enough to use up a small byte budget.
    padding = random.Random(seed).randbytes(400).hex() if seed is not None else ""
    return {"type": kind, "timestamp": int(seconds * 1000), "data": {"pad": padding}}


@pytest.mark.parametrize(
    ("name", "store"), _recording_stores(), ids=lambda value: value if isinstance(value, str) else ""
)
def test_recording_chunks_keep_their_order_and_stop_at_the_byte_budget(name: str, store: SessionStore) -> None:
    config = make_config()
    # The snapshot reaches the server before the batch that holds the page view.
    assert store.record_recording(
        _chunk(config, "sess-r", [_rrweb_event(4, NOW), _rrweb_event(2, NOW + 0.01)]), now=NOW
    )
    store.record(capture_events(config, visit("sess-r", NOW + 1, ["/pricing"]), NOW), now=NOW)
    results = [
        store.record_recording(_chunk(config, "sess-r", [_rrweb_event(3, NOW + 2 + index, seed=index)]), now=NOW)
        for index in range(10)
    ]
    kept = results.count(True)
    assert results[0] is True and results[-1] is False and results == [True] * kept + [False] * (10 - kept)

    (summary,) = store.sessions()
    assert (summary["entry_page"], summary["recording_chunks"], summary["recording_truncated"]) == (
        "/pricing",
        1 + kept,
        True,
    )
    assert (summary["first_seen"], summary["last_seen"], summary["duration_seconds"]) == (NOW, NOW + 11, 11)
    assert summary["recording_events"] == 2 + kept and summary["recording_started_at"] == NOW
    assert format_session(summary, NOW)["has_recording"] is True

    chunks = [json.loads(zlib.decompress(blob)) for blob in store.recording_chunks("sess-r")]
    assert [event["type"] for chunk in chunks for event in chunk] == [4, 2, *[3] * kept]
    assert [chunk[0]["timestamp"] for chunk in store_chunks(store, "sess-r", start=1)] == [
        int((NOW + 2 + index) * 1000) for index in range(kept)
    ]


def store_chunks(store: SessionStore, session_id: str, start: int) -> list[list[dict[str, Any]]]:
    return [json.loads(zlib.decompress(blob)) for blob in store.recording_chunks(session_id, start=start)]


def test_compute_user_hash_and_crm_traits() -> None:
    # Determinism & tenant salt isolation
    hash1 = compute_user_hash("site_a", "user_123")
    hash2 = compute_user_hash("site_a", "user_123")
    hash3 = compute_user_hash("site_b", "user_123")
    assert hash1 == hash2
    assert hash1 != hash3
    assert len(hash1) == 16
    assert compute_user_hash("site_a", "") == ""
    assert compute_user_hash("site_a", "anonymous") == ""

    # CRM traits & purchase telemetry folding
    config = make_config()
    store = SessionStore(MemoryBackend(config), config)
    items = [
        event_item("$pageview", "crm-sess", NOW, **{"$pathname": "/pricing"}),
        event_item(
            "$identify",
            "crm-sess",
            NOW + 5,
            **{
                "$identified_id": "usr-42",
                "$set": {
                    "name": "Alex Murphy",
                    "plan": "enterprise",
                    "role": "compliance_lead",
                    "company": "OCP Corp",
                    "cycle": "annual",
                },
            },
        ),
        event_item("$price_hesitation", "crm-sess", NOW + 10),
        event_item(
            "$purchase",
            "crm-sess",
            NOW + 20,
            **{"plan": "enterprise", "cycle": "annual", "value_tier": "$500+"},
        ),
    ]
    store.record(capture_events(config, items, NOW), now=NOW)

    (summary,) = store.sessions()
    assert summary["distinct_id"] == "usr-42"
    assert summary["is_registered"] is True
    assert summary["user_hash"] is not None
    assert summary["plan"] == "enterprise"
    assert summary["identity"]["role"] == "compliance_lead"
    assert summary["identity"]["company"] == "OCP Corp"
    assert summary["identity"]["value_tier"] == "$500+"
    assert summary["hesitation_detected"] is True
    assert summary["converted"] is True
    assert summary["conversion_event"] == "$purchase"

    # Payment failure telemetry folding
    store.record(
        capture_events(
            config,
            [
                event_item("$pageview", "fail-sess", NOW + 50, **{"$pathname": "/checkout"}),
                event_item("$payment_failed", "fail-sess", NOW + 55, **{"error_code": "insufficient_funds"}),
            ],
            NOW,
        ),
        now=NOW,
    )
    fail_summary = next(s for s in store.sessions() if s["session_id"] == "fail-sess")
    assert fail_summary["payment_failed"] is True
    assert fail_summary["payment_error_code"] == "insufficient_funds"
