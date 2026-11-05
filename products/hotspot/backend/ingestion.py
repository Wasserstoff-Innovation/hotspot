"""Turns a raw capture request body into validated, size-bounded events."""

from __future__ import annotations

import re
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from products.hotspot.backend.config import HotspotConfig
from products.hotspot.backend.useragent import parse_user_agent

MAX_EVENT_NAME_LENGTH = 200
MAX_ID_LENGTH = 200
MAX_STRING_LENGTH = 4000
MAX_PROPERTY_COUNT = 200
MAX_LIST_LENGTH = 100
MAX_DEPTH = 4
# Events older than this are kept at their own timestamp only if they fall inside the window;
# anything older is almost always a clock problem on the visitor's machine.
MAX_EVENT_AGE_SECONDS = 30 * 24 * 3600
MAX_FUTURE_SKEW_SECONDS = 120

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


def parse_batch(body: bytes, context: RequestContext, config: HotspotConfig, now: float) -> list[CapturedEvent]:
    if len(body) > config.max_body_bytes:
        raise IngestionError("Request body is too large", status=413)
    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except (UnicodeDecodeError, ValueError) as error:
        raise IngestionError("Body is not valid JSON") from error
    if not isinstance(payload, dict):
        raise IngestionError("Body must be a JSON object")

    api_key = _clean_id(payload.get("api_key"))
    if not api_key:
        raise IngestionError("api_key is required", status=401)
    if config.api_keys and api_key not in config.api_keys:
        raise IngestionError("Unknown api_key", status=401)

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
