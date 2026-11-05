from __future__ import annotations

import json
from typing import Any

from products.hotspot.backend.config import HotspotConfig, load_config
from products.hotspot.backend.ingestion import CapturedEvent, RequestContext, parse_batch
from products.hotspot.backend.service import HotspotService
from products.hotspot.backend.store import MemoryBackend, SessionStore

CHROME_MAC = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)
SAFARI_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.0 Mobile/15E148 Safari/604.1"
)


def make_config(**env: str) -> HotspotConfig:
    return load_config({"HOTSPOT_SECRET_KEY": "test-secret", **env})


def make_service(**env: str) -> HotspotService:
    config = make_config(**env)
    return HotspotService(config, SessionStore(MemoryBackend(config), config))


def event_item(name: str, session_id: str, timestamp: float, distinct_id: str = "visitor-1", /, **props: Any) -> dict:
    return {
        "event": name,
        "timestamp": timestamp,
        "properties": {"distinct_id": distinct_id, "$session_id": session_id, **props},
    }


def batch_body(items: list[Any], api_key: str = "hs_test") -> bytes:
    return json.dumps({"api_key": api_key, "batch": items}).encode()


def capture_events(
    config: HotspotConfig, items: list[dict], now: float, api_key: str = "hs_test", user_agent: str = CHROME_MAC
) -> list[CapturedEvent]:
    return parse_batch(batch_body(items, api_key), RequestContext(user_agent=user_agent), config, now)


def visit(session_id: str, start: float, pages: list[str], distinct_id: str = "visitor-1") -> list[dict]:
    """A plain visit: one pageview per page, 10 seconds apart."""
    return [
        event_item("$pageview", session_id, start + index * 10, distinct_id, **{"$pathname": page})
        for index, page in enumerate(pages)
    ]
