"""Turns a raw capture request body into validated, size-bounded events."""

from __future__ import annotations

import re
import json
import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from products.hotspot.backend.config import HotspotConfig
from products.hotspot.backend.geo import country_for_timezone
from products.hotspot.backend.useragent import parse_user_agent

MAX_EVENT_NAME_LENGTH = 200
MAX_ID_LENGTH = 200
MAX_STRING_LENGTH = 4000
MAX_PROPERTY_COUNT = 200
MAX_LIST_LENGTH = 100
MAX_DEPTH = 4
MAX_RECORDING_EVENTS = 5000
# Events older than this are kept at their own timestamp only if they fall inside the window;
# anything older is almost always a clock problem on the visitor's machine.
MAX_EVENT_AGE_SECONDS = 30 * 24 * 3600
MAX_FUTURE_SKEW_SECONDS = 120
# rrweb event types: DomContentLoaded, Load, FullSnapshot, IncrementalSnapshot, Meta, Custom, Plugin.
RECORDING_EVENT_TYPES = frozenset(range(7))

_ID_SAFE = re.compile(r"[^A-Za-z0-9_.:@-]")
_RESERVED_PROPERTY_KEYS = frozenset({"token", "api_key", "project_id", "distinct_id"})
_COUNTRY_HEADERS = ("cf-ipcountry", "x-country-code", "cloudfront-viewer-country", "x-vercel-ip-country")


class IngestionError(ValueError):
    """A request the capture endpoint must refuse, with the HTTP status to answer."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class RequestContext:
    """What the server knows about the visitor from the HTTP request itself."""

    user_agent: str | None = None
    country: str | None = None

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> RequestContext:
        lowered = {key.lower(): value for key, value in headers.items()}
        country = next((lowered[name] for name in _COUNTRY_HEADERS if lowered.get(name)), None)
        if country and (len(country) != 2 or not country.isalpha()):
            country = None
        return cls(user_agent=lowered.get("user-agent"), country=country.upper() if country else None)


@dataclass(frozen=True)
class CapturedEvent:
    event: str
    timestamp: float
    distinct_id: str
    session_id: str
    project_id: str
    properties: dict[str, Any]
    received_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "timestamp": self.timestamp,
            "distinct_id": self.distinct_id,
            "session_id": self.session_id,
            "project_id": self.project_id,
            "properties": self.properties,
            "received_at": self.received_at,
        }


@dataclass(frozen=True, kw_only=True)
class RecordingChunk:
    """One slice of a session's DOM recording: rrweb events, kept as the browser serialized them."""

    session_id: str
    distinct_id: str
    project_id: str
    events: list[dict[str, Any]]
    first_timestamp: float
    last_timestamp: float
    received_at: float

    @property
    def event_count(self) -> int:
        return len(self.events)


def decode_body(body: bytes, compression: str | None, limit: int) -> bytes:
    """Inflates a gzip body under a hard cap, so a small request cannot expand into a huge one."""
    if not compression:
        if len(body) > limit:
            raise IngestionError("Request body is too large", status=413)
        return body
    if compression != "gzip":
        raise IngestionError("Unsupported compression")
    inflater = zlib.decompressobj(wbits=31)
    try:
        data = inflater.decompress(body, limit + 1)
    except zlib.error as error:
        raise IngestionError("Body is not valid gzip") from error
    if len(data) > limit or inflater.unconsumed_tail:
        raise IngestionError("Request body is too large", status=413)
    return data


def parse_batch(body: bytes, context: RequestContext, config: HotspotConfig, now: float) -> list[CapturedEvent]:
    payload = _load_object(decode_body(body, None, config.max_body_bytes))
    api_key = _check_api_key(payload, config)

    raw_items = payload.get("batch")
    if raw_items is None:
        raw_items = [payload] if "event" in payload else []
    if not isinstance(raw_items, list):
        raise IngestionError("batch must be a list")
    if len(raw_items) > config.max_batch_size:
        raise IngestionError(f"batch holds more than {config.max_batch_size} events", status=413)

    agent = parse_user_agent(context.user_agent)
    server_properties: dict[str, Any] = {}
    if agent.browser:
        server_properties["$browser"] = agent.browser
    if agent.browser_version:
        server_properties["$browser_version"] = agent.browser_version
    if agent.os:
        server_properties["$os"] = agent.os
    if agent.device_type:
        server_properties["$device_type"] = agent.device_type
    if context.country:
        server_properties["$country"] = context.country

    events: list[CapturedEvent] = []
    for item in raw_items:
        event = _parse_item(item, api_key, server_properties, now)
        if event is not None:
            events.append(event)
    return events


def parse_recording(body: bytes, compression: str | None, config: HotspotConfig, now: float) -> RecordingChunk:
    payload = _load_object(decode_body(body, compression, config.max_recording_body_bytes))
    api_key = _check_api_key(payload, config)
    session_id = _clean_id(payload.get("session_id"))
    if not session_id:
        raise IngestionError("session_id is required")
    raw_events = payload.get("events")
    if not isinstance(raw_events, list):
        raise IngestionError("events must be a list")
    if len(raw_events) > MAX_RECORDING_EVENTS:
        raise IngestionError(f"events holds more than {MAX_RECORDING_EVENTS} items", status=413)
    events = [item for item in raw_events if _is_recording_event(item)]
    if not events:
        raise IngestionError("events holds no recording events")
    # rrweb stamps events in milliseconds; the rest of Hotspot works in seconds.
    timestamps = [float(item["timestamp"]) / 1000 for item in events]
    first, last = min(timestamps), max(timestamps)
    if first < now - MAX_EVENT_AGE_SECONDS or last > now + MAX_FUTURE_SKEW_SECONDS:
        raise IngestionError("Recording timestamps are out of range")
    return RecordingChunk(
        session_id=session_id,
        distinct_id=_clean_id(payload.get("distinct_id")) or "anonymous",
        project_id=api_key,
        events=events,
        first_timestamp=first,
        last_timestamp=last,
        received_at=now,
    )


def _is_recording_event(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    kind = item.get("type")
    stamp = item.get("timestamp")
    if isinstance(kind, bool) or not isinstance(kind, int) or kind not in RECORDING_EVENT_TYPES:
        return False
    if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
        return False
    return stamp == stamp and abs(stamp) != float("inf")


def _load_object(body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except (UnicodeDecodeError, ValueError) as error:
        raise IngestionError("Body is not valid JSON") from error
    if not isinstance(payload, dict):
        raise IngestionError("Body must be a JSON object")
    return payload


def _check_api_key(payload: Mapping[str, Any], config: HotspotConfig) -> str:
    api_key = _clean_id(payload.get("api_key"))
    if not api_key:
        raise IngestionError("api_key is required", status=401)
    if config.api_keys and api_key not in config.api_keys:
        raise IngestionError("Unknown api_key", status=401)
    return api_key


def _parse_item(item: Any, api_key: str, server_properties: dict[str, Any], now: float) -> CapturedEvent | None:
    if not isinstance(item, dict):
        return None
    name = item.get("event")
    if not isinstance(name, str) or not name.strip():
        return None
    name = name.strip()[:MAX_EVENT_NAME_LENGTH]

    raw_properties = item.get("properties")
    properties = _sanitize_value(raw_properties, depth=0) if isinstance(raw_properties, dict) else {}
    assert isinstance(properties, dict)

    distinct_id = _clean_id(properties.get("distinct_id")) or _clean_id(item.get("distinct_id")) or "anonymous"
    session_id = _clean_id(properties.get("$session_id")) or f"s-{distinct_id}"
    for key in _RESERVED_PROPERTY_KEYS:
        properties.pop(key, None)
    for key, value in server_properties.items():
        properties.setdefault(key, value)
    # Without a CDN in front, the browser's timezone is the only hint of where the visitor is.
    if "$country" not in properties:
        country = country_for_timezone(properties.get("$timezone"))
        if country:
            properties["$country"] = country

    return CapturedEvent(
        event=name,
        timestamp=_parse_timestamp(item.get("timestamp"), now),
        distinct_id=distinct_id,
        session_id=session_id,
        project_id=api_key,
        properties=properties,
        received_at=now,
    )


def _clean_id(value: Any) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        return ""
    return _ID_SAFE.sub("", value.strip())[:MAX_ID_LENGTH]


def _parse_timestamp(value: Any, now: float) -> float:
    parsed: float | None = None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        parsed = float(value) / 1000 if value > 1e11 else float(value)
    elif isinstance(value, str) and value:
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=UTC)
            parsed = moment.timestamp()
        except ValueError:
            parsed = None
    if parsed is None or parsed > now + MAX_FUTURE_SKEW_SECONDS or parsed < now - MAX_EVENT_AGE_SECONDS:
        return now
    return parsed


def _sanitize_value(value: Any, depth: int) -> Any:
    if isinstance(value, str):
        return value[:MAX_STRING_LENGTH]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value if value == value and abs(value) != float("inf") else None
    if depth >= MAX_DEPTH:
        return None
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, inner in list(value.items())[:MAX_PROPERTY_COUNT]:
            if isinstance(key, str):
                cleaned[key[:MAX_EVENT_NAME_LENGTH]] = _sanitize_value(inner, depth + 1)
        return cleaned
    if isinstance(value, list):
        return [_sanitize_value(inner, depth + 1) for inner in value[:MAX_LIST_LENGTH]]
    return str(value)[:MAX_STRING_LENGTH]
