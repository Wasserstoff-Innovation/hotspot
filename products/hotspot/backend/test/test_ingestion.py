from __future__ import annotations

import gzip
import json

import pytest

from parameterized import parameterized

from products.hotspot.backend.ingestion import IngestionError, RequestContext, parse_batch, parse_recording
from products.hotspot.backend.test.helpers import CHROME_MAC, SAFARI_IPHONE, batch_body, event_item, make_config

NOW = 1_700_000_000.0
NOW_MS = 1_700_000_000_000


class TestApiKeys:
    def test_unknown_key_is_refused_when_an_allowlist_is_set(self) -> None:
        config = make_config(HOTSPOT_API_KEYS="hs_a, hs_b")
        with pytest.raises(IngestionError) as refused:
            parse_batch(batch_body([event_item("$pageview", "s1", NOW)], api_key="hs_c"), RequestContext(), config, NOW)
        assert refused.value.status == 401

        accepted = parse_batch(
            batch_body([event_item("$pageview", "s1", NOW)], api_key="hs_b"), RequestContext(), config, NOW
        )
        assert [event.project_id for event in accepted] == ["hs_b"]

    def test_missing_key_is_refused_even_without_an_allowlist(self) -> None:
        config = make_config()
        with pytest.raises(IngestionError) as refused:
            parse_batch(
                json.dumps({"batch": [event_item("$pageview", "s1", NOW)]}).encode(), RequestContext(), config, NOW
            )
        assert refused.value.status == 401


class TestNormalization:
    def test_ids_come_from_properties_and_reserved_keys_are_dropped(self) -> None:
        item = event_item("$autocapture", "sess-1", NOW, "user@example.com", token="secret", api_key="x")
        (event,) = parse_batch(batch_body([item]), RequestContext(user_agent=CHROME_MAC), make_config(), NOW)
        assert (event.session_id, event.distinct_id, event.project_id) == ("sess-1", "user@example.com", "hs_test")
        assert "token" not in event.properties and "api_key" not in event.properties
        assert (event.properties["$browser"], event.properties["$os"], event.properties["$device_type"]) == (
            "Chrome",
            "macOS",
            "desktop",
        )

    def test_mobile_user_agent_and_country_header_are_recorded(self) -> None:
        context = RequestContext.from_headers({"User-Agent": SAFARI_IPHONE, "CF-IPCountry": "de"})
        (event,) = parse_batch(batch_body([event_item("$pageview", "s", NOW)]), context, make_config(), NOW)
        assert (event.properties["$browser"], event.properties["$os"], event.properties["$device_type"]) == (
            "Safari",
            "iOS",
            "mobile",
        )
        assert event.properties["$country"] == "DE"

    @parameterized.expand(
        [
            ("iso_string", "2023-11-14T22:13:20Z", NOW),
            ("milliseconds", NOW * 1000 - 5000, NOW - 5),
            ("too_far_in_the_future", NOW + 3600, NOW),
            ("unparseable", "yesterday", NOW),
        ]
    )
    def test_timestamps_are_parsed_and_clamped(self, _name: str, raw: object, expected: float) -> None:
        item = event_item("$pageview", "s", 0)
        item["timestamp"] = raw
        (event,) = parse_batch(batch_body([item]), RequestContext(), make_config(), NOW)
        assert event.timestamp == pytest.approx(expected)

    def test_oversized_strings_and_batches_are_bounded(self) -> None:
        config = make_config(HOTSPOT_MAX_BATCH_SIZE="2")
        item = event_item("$exception", "s", NOW, **{"$exception_stack": "x" * 10_000})
        (event,) = parse_batch(batch_body([item]), RequestContext(), config, NOW)
        assert len(event.properties["$exception_stack"]) == 4000

        with pytest.raises(IngestionError) as refused:
            parse_batch(batch_body([item, item, item]), RequestContext(), config, NOW)
        assert refused.value.status == 413

    def test_items_without_an_event_name_are_skipped_not_fatal(self) -> None:
        items = [{"properties": {}}, "junk", event_item("$pageview", "s", NOW)]
        events = parse_batch(batch_body(items), RequestContext(), make_config(), NOW)
        assert [event.event for event in events] == ["$pageview"]


class TestRecordings:
    def test_recording_bodies_are_inflated_under_a_cap_and_validated(self) -> None:
        config = make_config(HOTSPOT_MAX_RECORDING_BODY_BYTES="4096")
        events = [
            {"type": 4, "timestamp": NOW_MS, "data": {"href": "https://example.com/", "width": 1280, "height": 800}},
            {"type": 2, "timestamp": NOW_MS + 5000, "data": {"node": {}}},
            {"type": "3", "timestamp": NOW_MS + 6000},
            {"type": 3, "timestamp": "soon"},
            "not an event",
        ]
        payload = {"api_key": "hs_test", "session_id": "sess-1", "distinct_id": "v-1", "events": events}
        chunk = parse_recording(gzip.compress(json.dumps(payload).encode()), "gzip", config, NOW)
        assert [event["type"] for event in chunk.events] == [4, 2]
        assert (chunk.first_timestamp, chunk.last_timestamp) == (NOW, NOW + 5)
        assert (chunk.session_id, chunk.distinct_id, chunk.project_id) == ("sess-1", "v-1", "hs_test")

        bomb = gzip.compress(b'{"api_key":"hs_test","session_id":"s","events":[' + b"0," * 100_000 + b"0]}")
        assert len(bomb) < 4096
        with pytest.raises(IngestionError) as refused:
            parse_recording(bomb, "gzip", config, NOW)
        assert refused.value.status == 413

        stale = {**payload, "events": [{"type": 4, "timestamp": 1000, "data": {}}]}
        with pytest.raises(IngestionError, match="out of range"):
            parse_recording(json.dumps(stale).encode(), None, config, NOW)
