"""The framework-neutral application layer. Django views and the standalone server both call it."""

from __future__ import annotations

import json
import time
import zlib
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from products.hotspot import __version__
from products.hotspot.backend.analytics import OverviewQuery, build_overview, format_session, setup_info
from products.hotspot.backend.auth import make_dashboard_token, password_matches, verify_dashboard_token
from products.hotspot.backend.config import HotspotConfig, load_config
from products.hotspot.backend.ingestion import IngestionError, RequestContext, parse_batch, parse_recording
from products.hotspot.backend.store import SessionStore, build_store

logger = logging.getLogger(__name__)

PRODUCT_PATH = Path(__file__).resolve().parent.parent
SDK_PATH = PRODUCT_PATH / "sdk" / "hotspot.js"
# The files served under /hotspot/assets/: the rrweb recorder the script loads on visitors' pages, the
# player the dashboard uses and the world map it draws. This allowlist is the only way a request
# reaches the vendor directory.
ASSETS: dict[str, tuple[Path, str]] = {
    "recorder.js": (PRODUCT_PATH / "vendor" / "rrweb-record.js", "application/javascript; charset=utf-8"),
    "replay.js": (PRODUCT_PATH / "vendor" / "rrweb-replay.js", "application/javascript; charset=utf-8"),
    "replay.css": (PRODUCT_PATH / "vendor" / "rrweb-replay.css", "text/css; charset=utf-8"),
    "world.json": (PRODUCT_PATH / "vendor" / "world-map.json", "application/json; charset=utf-8"),
}
MAX_FEED_EVENTS = 500
MAX_RECORDING_CURSOR = 1_000_000
FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" fill="none">'
    '<circle cx="16" cy="16" r="14" stroke="#2563EB" stroke-width="2" stroke-dasharray="3 3" opacity="0.45"/>'
    '<circle cx="16" cy="16" r="9.5" stroke="#2563EB" stroke-width="2.5" opacity="0.85"/>'
    '<circle cx="16" cy="16" r="5.2" fill="#10B981"/></svg>'
)

PAGE_TITLES = {
    "landing": "Hotspot: see where your users get stuck",
    "dashboard": "Hotspot dashboard",
    "demo": "Sample site for testing Hotspot",
    "login": "Sign in to the Hotspot dashboard",
}


class HotspotService:
    def __init__(self, config: HotspotConfig, store: SessionStore) -> None:
        self.config = config
        self.store = store
        self._assets: dict[str, str] = {}
        if not config.dashboard_protected:
            logger.warning("HOTSPOT_DASHBOARD_PASSWORD is not set: the Hotspot dashboard is open to anyone")
        if not config.api_keys:
            logger.warning("HOTSPOT_API_KEYS is not set: the capture endpoint accepts any api_key")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, redis_client: Any | None = None) -> HotspotService:
        config = load_config(env)
        return cls(config, build_store(config, redis_client))

    def capture(self, body: bytes, headers: Mapping[str, str], now: float | None = None) -> tuple[int, dict[str, Any]]:
        moment = time.time() if now is None else now
        try:
            events = parse_batch(body, RequestContext.from_headers(headers), self.config, moment)
        except IngestionError as error:
            return error.status, {"status": 0, "error": str(error)}
        received = self.store.record(events, now=moment)
        return 200, {"status": 1, "received": received}

    def capture_recording(
        self, body: bytes, params: Mapping[str, str], now: float | None = None
    ) -> tuple[int, dict[str, Any]]:
        moment = time.time() if now is None else now
        if not self.config.replay_enabled:
            return 403, {"status": 0, "error": "Session replay is turned off on this server"}
        try:
            chunk = parse_recording(body, params.get("compression") or None, self.config, moment)
        except IngestionError as error:
            return error.status, {"status": 0, "error": str(error)}
        stored = self.store.record_recording(chunk, now=moment)
        return 200, {"status": 1, "received": chunk.event_count, "stored": stored}

    def overview(self, params: Mapping[str, str], now: float | None = None) -> dict[str, Any]:
        moment = time.time() if now is None else now
        return build_overview(self.store, OverviewQuery.from_params(params), self.config, moment)

    def session_detail(self, session_id: str, now: float | None = None) -> dict[str, Any] | None:
        moment = time.time() if now is None else now
        detail = self.store.session(session_id)
        if detail is None:
            return None
        events = sorted(detail.pop("events", []), key=lambda event: float(event.get("timestamp") or 0))
        return {
            "status": 1,
            "session": format_session(detail, moment),
            "events": [
                {
                    "event": event.get("event"),
                    "timestamp": event.get("timestamp"),
                    "properties": event.get("properties") or {},
                }
                for event in events
            ],
        }

    def recording(self, session_id: str, params: Mapping[str, str]) -> str | None:
        """The recording as JSON text. Chunks are spliced in as stored, so megabytes of events are not re-parsed."""
        summary = self.store.summary(session_id)
        if summary is None:
            return None
        start = _int_param(params.get("from"), default=0, low=0, high=MAX_RECORDING_CURSOR)
        chunks = self.store.recording_chunks(session_id, start)
        parts = []
        for chunk in chunks:
            inner = zlib.decompress(chunk).decode("utf-8").strip()[1:-1]
            if inner:
                parts.append(inner)
        head = json.dumps(
            {
                "status": 1,
                "session_id": session_id,
                "from": start,
                "next": start + len(chunks),
                "truncated": bool(summary.get("recording_truncated")),
                "started_at": summary.get("recording_started_at"),
            },
            separators=(",", ":"),
        )
        return f'{head[:-1]},"events":[{",".join(parts)}]}}'

    def events_feed(self, params: Mapping[str, str], now: float | None = None) -> dict[str, Any]:
        moment = time.time() if now is None else now
        since = _float_param(params.get("since"))
        limit = _int_param(params.get("limit"), default=100, low=1, high=MAX_FEED_EVENTS)
        events = self.store.recent_events(limit=limit, since=since)
        return {
            "status": 1,
            "now": moment,
            "events": [
                {
                    "event": event.get("event"),
                    "timestamp": event.get("timestamp"),
                    "session_id": event.get("session_id"),
                    "distinct_id": event.get("distinct_id"),
                    "properties": event.get("properties") or {},
                }
                for event in events
            ],
        }

    def login(self, password: str | None, now: float | None = None) -> str | None:
        moment = time.time() if now is None else now
        if not password_matches(self.config.dashboard_password, password):
            return None
        return make_dashboard_token(self.config.secret_key, moment)

    def is_authorized(self, cookie_token: str | None, authorization: str | None, now: float | None = None) -> bool:
        if not self.config.dashboard_protected:
            return True
        moment = time.time() if now is None else now
        if verify_dashboard_token(self.config.secret_key, cookie_token, moment):
            return True
        scheme, _, credential = (authorization or "").partition(" ")
        return scheme.lower() == "bearer" and password_matches(self.config.dashboard_password, credential.strip())

    def page_context(self, page: str, nonce: str, **extra: str) -> dict[str, str]:
        context = {
            "page_title": PAGE_TITLES.get(page, "Hotspot"),
            "site_url": self.config.site_url,
            "sdk_url": self.config.sdk_url,
            "api_key": self.config.api_key_for_pages,
            "csp_nonce": nonce,
            "version": __version__,
            "dashboard_protected": "1" if self.config.dashboard_protected else "",
            "demo_enabled": "1" if self.config.demo_enabled else "",
            "replay_enabled": "1" if self.config.replay_enabled else "",
            "storage": self.store.backend_name,
        }
        context.update(extra)
        return context

    def setup(self) -> dict[str, Any]:
        return setup_info(self.store, self.config)

    def sdk_source(self) -> str:
        return SDK_PATH.read_text(encoding="utf-8")

    def asset(self, name: str) -> tuple[str, str] | None:
        entry = ASSETS.get(name)
        if entry is None:
            return None
        path, content_type = entry
        if name not in self._assets:
            self._assets[name] = path.read_text(encoding="utf-8")
        return self._assets[name], content_type

    def llms_txt(self) -> str:
        return (
            "# Hotspot\n"
            "> Self-hosted product telemetry: one script tag captures page views, clicks, rage clicks, "
            "dead clicks, scroll depth, form submits, JavaScript errors, web vitals and a DOM recording of "
            "each session. The dashboard replays sessions with console and network activity, and shows "
            "funnels, paths, friction, pages, referrers and errors.\n\n"
            f"- Dashboard: {self.config.site_url}/dashboard/\n"
            f"- Sample site: {self.config.site_url}/demo/\n"
            f"- Script: {self.config.sdk_url}\n"
            f"- Capture endpoint: {self.config.site_url}/hotspot/batch/\n"
            f"- Recording endpoint: {self.config.site_url}/hotspot/replay/\n"
        )


_service: HotspotService | None = None


def get_service() -> HotspotService:
    global _service
    if _service is None:
        _service = HotspotService.from_env()
    return _service


def reset_service(service: HotspotService | None = None) -> None:
    global _service
    _service = service


def _float_param(value: str | None) -> float | None:
    try:
        return float(value) if value else None
    except ValueError:
        return None


def _int_param(value: str | None, default: int, low: int, high: int) -> int:
    try:
        return min(high, max(low, int(value))) if value else default
    except ValueError:
        return default
