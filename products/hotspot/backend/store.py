"""Session and event storage.

Events are folded into one summary per session as they arrive, so the dashboard reads
summaries rather than re-scanning raw events. Redis is the shared store across worker
processes; the in-memory backend keeps a single process working without it.
"""

from __future__ import annotations

import re
import json
import time
import zlib
import logging
import secrets
import threading
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Protocol
from urllib.parse import urlsplit

from products.hotspot.backend.config import HotspotConfig
from products.hotspot.backend.ingestion import CapturedEvent, RecordingChunk

logger = logging.getLogger(__name__)

KEY_PREFIX = "hotspot:v1:"
MAX_PAGES_PER_SESSION = 200
RECORDING_COMPRESSION_LEVEL = 6
_IDENTITY_KEYS = {
    "email": ("email", "$email"),
    "name": ("name", "$name", "full_name"),
    "company": ("company", "$company", "organization", "org"),
}
_SESSION_PROPERTY_FIELDS = {
    "$browser": "browser",
    "$browser_version": "browser_version",
    "$os": "os",
    "$device_type": "device_type",
    "$country": "country",
    "$viewport_width": "viewport_width",
    "$viewport_height": "viewport_height",
    "$screen_width": "screen_width",
    "$screen_height": "screen_height",
    "$browser_language": "language",
    "$timezone": "timezone",
}

SessionMutator = Callable[[dict[str, Any] | None], dict[str, Any]]


class StorageBackend(Protocol):
    name: str

    def update_session(
        self,
        session_id: str,
        mutate: SessionMutator,
        events_json: list[str],
        last_seen: float,
        recording_chunk: bytes | None = None,
    ) -> dict[str, Any]: ...

    def get_sessions(self, session_ids: list[str]) -> list[dict[str, Any] | None]: ...

    def get_session_events(self, session_id: str) -> list[dict[str, Any]]: ...

    def get_recording(self, session_id: str, start: int) -> list[bytes]: ...

    def session_ids(self) -> list[str]: ...

    def push_events(self, events_json: list[str]) -> None: ...

    def recent_events(self, limit: int) -> list[dict[str, Any]]: ...

    def prune(self, before: float) -> None: ...

    def clear(self) -> None: ...

    def save_site(self, site: dict[str, Any]) -> None: ...

    def get_sites(self) -> list[dict[str, Any]]: ...

    def get_site(self, site_id: str) -> dict[str, Any] | None: ...

    def delete_site(self, site_id: str) -> bool: ...


class MemoryBackend:
    name = "memory"

    def __init__(self, config: HotspotConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._sessions: dict[str, dict[str, Any]] = {}
        self._session_events: dict[str, deque[str]] = {}
        self._recordings: dict[str, list[bytes]] = {}
        self._last_seen: dict[str, float] = {}
        self._events: deque[str] = deque(maxlen=config.max_events)
        self._sites: dict[str, dict[str, Any]] = {}

    def update_session(
        self,
        session_id: str,
        mutate: SessionMutator,
        events_json: list[str],
        last_seen: float,
        recording_chunk: bytes | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            updated = mutate(self._sessions.get(session_id))
            self._sessions[session_id] = updated
            self._last_seen[session_id] = float(updated.get("last_seen") or last_seen)
            bucket = self._session_events.setdefault(session_id, deque(maxlen=self._config.max_session_events))
            bucket.extend(events_json)
            if recording_chunk is not None:
                self._recordings.setdefault(session_id, []).append(recording_chunk)
            return updated

    def get_sessions(self, session_ids: list[str]) -> list[dict[str, Any] | None]:
        with self._lock:
            return [self._sessions.get(session_id) for session_id in session_ids]

    def get_session_events(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [json.loads(raw) for raw in self._session_events.get(session_id, ())]

    def get_recording(self, session_id: str, start: int) -> list[bytes]:
        with self._lock:
            return list(self._recordings.get(session_id, [])[start:])

    def session_ids(self) -> list[str]:
        with self._lock:
            return sorted(self._last_seen, key=lambda session_id: self._last_seen[session_id], reverse=True)

    def push_events(self, events_json: list[str]) -> None:
        with self._lock:
            self._events.extend(events_json)

    def recent_events(self, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            newest_first = list(self._events)[-limit:][::-1]
        return [json.loads(raw) for raw in newest_first]

    def prune(self, before: float) -> None:
        with self._lock:
            for session_id in [sid for sid, seen in self._last_seen.items() if seen < before]:
                self._sessions.pop(session_id, None)
                self._session_events.pop(session_id, None)
                self._recordings.pop(session_id, None)
                self._last_seen.pop(session_id, None)

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()
            self._session_events.clear()
            self._recordings.clear()
            self._last_seen.clear()
            self._events.clear()
            self._sites.clear()

    def save_site(self, site: dict[str, Any]) -> None:
        with self._lock:
            self._sites[site["id"]] = dict(site)

    def get_sites(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._sites.values())

    def get_site(self, site_id: str) -> dict[str, Any] | None:
        with self._lock:
            site = self._sites.get(site_id)
            return dict(site) if site else None

    def delete_site(self, site_id: str) -> bool:
        with self._lock:
            return self._sites.pop(site_id, None) is not None


class RedisBackend:
    name = "redis"

    def __init__(self, client: Any, config: HotspotConfig, prefix: str = KEY_PREFIX) -> None:
        self._client = client
        self._config = config
        self._prefix = prefix
        self._events_key = f"{prefix}events"
        self._index_key = f"{prefix}sessions"
        self._sites_key = f"{prefix}sites"

    def _session_key(self, session_id: str) -> str:
        return f"{self._prefix}s:{session_id}"

    def _events_key_for(self, session_id: str) -> str:
        return f"{self._prefix}s:{session_id}:events"

    def _recording_key_for(self, session_id: str) -> str:
        return f"{self._prefix}s:{session_id}:rec"

    def update_session(
        self,
        session_id: str,
        mutate: SessionMutator,
        events_json: list[str],
        last_seen: float,
        recording_chunk: bytes | None = None,
    ) -> dict[str, Any]:
        key = self._session_key(session_id)
        ttl = self._config.session_ttl_seconds

        def transaction(pipe: Any) -> dict[str, Any]:
            raw = pipe.get(key)
            updated = mutate(json.loads(_text(raw)) if raw else None)
            pipe.multi()
            pipe.set(key, json.dumps(updated, separators=(",", ":")), ex=ttl)
            pipe.zadd(self._index_key, {session_id: float(updated.get("last_seen") or last_seen)})
            if events_json:
                events_key = self._events_key_for(session_id)
                pipe.rpush(events_key, *events_json)
                pipe.ltrim(events_key, -self._config.max_session_events, -1)
                pipe.expire(events_key, ttl)
            if recording_chunk is not None:
                recording_key = self._recording_key_for(session_id)
                pipe.rpush(recording_key, recording_chunk)
                pipe.expire(recording_key, ttl)
            return updated

        result: dict[str, Any] = self._client.transaction(transaction, key, value_from_callable=True)
        return result

    def get_sessions(self, session_ids: list[str]) -> list[dict[str, Any] | None]:
        if not session_ids:
            return []
        raws = self._client.mget([self._session_key(session_id) for session_id in session_ids])
        return [json.loads(_text(raw)) if raw else None for raw in raws]

    def get_session_events(self, session_id: str) -> list[dict[str, Any]]:
        raws = self._client.lrange(self._events_key_for(session_id), 0, -1)
        return [json.loads(_text(raw)) for raw in raws]

    def get_recording(self, session_id: str, start: int) -> list[bytes]:
        return [_binary(raw) for raw in self._client.lrange(self._recording_key_for(session_id), start, -1)]

    def session_ids(self) -> list[str]:
        return [_text(raw) for raw in self._client.zrevrange(self._index_key, 0, -1)]

    def push_events(self, events_json: list[str]) -> None:
        if not events_json:
            return
        pipe = self._client.pipeline()
        pipe.lpush(self._events_key, *events_json)
        pipe.ltrim(self._events_key, 0, self._config.max_events - 1)
        pipe.execute()

    def recent_events(self, limit: int) -> list[dict[str, Any]]:
        raws = self._client.lrange(self._events_key, 0, max(0, limit - 1))
        return [json.loads(_text(raw)) for raw in raws]

    def prune(self, before: float) -> None:
        self._client.zremrangebyscore(self._index_key, "-inf", before)

    def clear(self) -> None:
        keys = list(self._client.scan_iter(match=f"{self._prefix}*"))
        if keys:
            self._client.delete(*keys)

    def save_site(self, site: dict[str, Any]) -> None:
        raw = json.dumps(site, separators=(",", ":"))
        self._client.hset(self._sites_key, site["id"], raw)

    def get_sites(self) -> list[dict[str, Any]]:
        raws = self._client.hgetall(self._sites_key)
        sites = []
        for raw in raws.values():
            try:
                sites.append(json.loads(_text(raw)))
            except Exception:
                pass
        return sites

    def get_site(self, site_id: str) -> dict[str, Any] | None:
        raw = self._client.hget(self._sites_key, site_id)
        if not raw:
            return None
        try:
            return json.loads(_text(raw))
        except Exception:
            return None

    def delete_site(self, site_id: str) -> bool:
        return bool(self._client.hdel(self._sites_key, site_id))


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _binary(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    raise TypeError("Recording chunks need a Redis client that returns bytes, not decoded strings")


class SessionStore:
    def __init__(self, backend: StorageBackend, config: HotspotConfig) -> None:
        self._backend = backend
        self._config = config

    @property
    def backend_name(self) -> str:
        return self._backend.name

    def record(self, events: Iterable[CapturedEvent], now: float | None = None) -> int:
        ordered = sorted(events, key=lambda event: event.timestamp)
        if not ordered:
            return 0
        moment = time.time() if now is None else now
        by_session: dict[str, list[CapturedEvent]] = {}
        for event in ordered:
            by_session.setdefault(event.session_id, []).append(event)
            host = event.properties.get("$host")
            if host and isinstance(host, str):
                host_clean = host.strip().lower().split(":")[0]
                if host_clean and host_clean not in ("localhost", "127.0.0.1"):
                    registered = [s.get("domain") for s in self._backend.get_sites()]
                    if host_clean not in registered:
                        self.save_site(host_clean, host_clean, event.project_id)

        for session_id, session_events in by_session.items():
            events_json = [json.dumps(event.to_dict(), separators=(",", ":")) for event in session_events]
            mutator = _build_mutator(session_events, self._config)
            self._backend.update_session(session_id, mutator, events_json, session_events[-1].timestamp)

        self._backend.push_events([json.dumps(event.to_dict(), separators=(",", ":")) for event in ordered])
        self._backend.prune(moment - self._config.session_ttl_seconds)
        return len(ordered)

    def sessions(self, limit: int | None = None) -> list[dict[str, Any]]:
        session_ids = self._backend.session_ids()
        if limit is not None:
            session_ids = session_ids[:limit]
        return [summary for summary in self._backend.get_sessions(session_ids) if summary]

    def session(self, session_id: str) -> dict[str, Any] | None:
        summary = self._backend.get_sessions([session_id])[0]
        if summary is None:
            return None
        detail = dict(summary)
        detail["events"] = self._backend.get_session_events(session_id)
        return detail

    def session_events(self, session_id: str) -> list[dict[str, Any]]:
        return self._backend.get_session_events(session_id)

    def summary(self, session_id: str) -> dict[str, Any] | None:
        return self._backend.get_sessions([session_id])[0]

    def record_recording(self, chunk: RecordingChunk, now: float | None = None) -> bool:
        """Stores one slice of a DOM recording; returns False once the session has used its byte budget."""
        moment = time.time() if now is None else now
        blob = zlib.compress(
            json.dumps(chunk.events, separators=(",", ":")).encode("utf-8"), RECORDING_COMPRESSION_LEVEL
        )
        used = int((self.summary(chunk.session_id) or {}).get("recording_bytes") or 0)
        fits = used + len(blob) <= self._config.max_recording_bytes
        mutator = _recording_mutator(chunk, len(blob) if fits else None)
        self._backend.update_session(
            chunk.session_id, mutator, [], chunk.last_timestamp, recording_chunk=blob if fits else None
        )
        self._backend.prune(moment - self._config.session_ttl_seconds)
        return fits

    def recording_chunks(self, session_id: str, start: int = 0) -> list[bytes]:
        return self._backend.get_recording(session_id, max(0, start))

    def recent_events(self, limit: int = 100, since: float | None = None) -> list[dict[str, Any]]:
        events = self._backend.recent_events(limit)
        if since is not None:
            events = [event for event in events if event.get("timestamp", 0) > since]
        return events

    def clear(self) -> None:
        self._backend.clear()

    def save_site(self, name: str, domain: str, api_key: str | None = None) -> dict[str, Any]:
        domain_clean = (
            domain.strip().lower().removeprefix("http://").removeprefix("https://").split("/")[0].split(":")[0]
        )
        site_id = _slugify(domain_clean)
        key = api_key or generate_api_key(domain_clean)
        site = {
            "id": site_id,
            "name": name.strip() or domain_clean,
            "domain": domain_clean,
            "api_key": key,
            "created_at": time.time(),
        }
        self._backend.save_site(site)
        return site

    def sites(self) -> list[dict[str, Any]]:
        existing = self._backend.get_sites()
        if not existing:
            self.ensure_default_sites()
            existing = self._backend.get_sites()
        return existing

    def site(self, site_id: str) -> dict[str, Any] | None:
        return self._backend.get_site(site_id)

    def delete_site(self, site_id: str) -> bool:
        return self._backend.delete_site(site_id)

    def all_api_keys(self) -> frozenset[str]:
        keys = {s["api_key"] for s in self._backend.get_sites() if s.get("api_key")}
        return frozenset(keys)

    def ensure_default_sites(self) -> None:
        self.save_site("Buxa AI", "buxa.ai", "hs_live_buxa_ai")
        host = urlsplit(self._config.site_url).hostname
        if host and host != "buxa.ai":
            self.save_site("Hotspot Console", host, self._config.api_key_for_pages)


def _slugify(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return cleaned or "site"


def generate_api_key(domain: str) -> str:
    slug = _slugify(domain.replace(".", "-"))[:15]
    rand = secrets.token_hex(4)
    return f"hs_live_{slug}_{rand}"


def _build_mutator(events: list[CapturedEvent], config: HotspotConfig) -> SessionMutator:
    first = events[0]

    def mutate(current: dict[str, Any] | None) -> dict[str, Any]:
        summary = (
            dict(current)
            if current
            else new_session_summary(first.session_id, first.distinct_id, first.project_id, first.timestamp)
        )
        for event in events:
            apply_event(summary, event, config)
        return summary

    return mutate


def _extend_span(summary: dict[str, Any], first: float, last: float) -> None:
    summary["first_seen"] = min(summary["first_seen"], first)
    summary["last_seen"] = max(summary["last_seen"], last)
    summary["duration_seconds"] = max(0, int(round(summary["last_seen"] - summary["first_seen"])))


def _recording_mutator(chunk: RecordingChunk, stored_bytes: int | None) -> SessionMutator:
    """Folds a recording chunk into the summary; a chunk over budget only marks the recording as cut short."""

    def mutate(current: dict[str, Any] | None) -> dict[str, Any]:
        summary = (
            dict(current)
            if current
            else new_session_summary(chunk.session_id, chunk.distinct_id, chunk.project_id, chunk.first_timestamp)
        )
        _extend_span(summary, chunk.first_timestamp, chunk.last_timestamp)
        if stored_bytes is None:
            summary["recording_truncated"] = True
            return summary
        summary["recording_bytes"] = int(summary.get("recording_bytes") or 0) + stored_bytes
        summary["recording_events"] = int(summary.get("recording_events") or 0) + chunk.event_count
        summary["recording_chunks"] = int(summary.get("recording_chunks") or 0) + 1
        started = summary.get("recording_started_at")
        summary["recording_started_at"] = (
            chunk.first_timestamp if started is None else min(float(started), chunk.first_timestamp)
        )
        return summary

    return mutate


def new_session_summary(session_id: str, distinct_id: str, project_id: str, timestamp: float) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "distinct_id": distinct_id,
        "project_id": project_id,
        "host": None,
        "first_seen": timestamp,
        "last_seen": timestamp,
        "duration_seconds": 0,
        "event_count": 0,
        "pageview_count": 0,
        "click_count": 0,
        "dead_click_count": 0,
        "rage_click_count": 0,
        "form_submit_count": 0,
        "error_count": 0,
        "custom_event_count": 0,
        "active_seconds": 0,
        "max_scroll_depth": None,
        "pages": [],
        "entry_page": None,
        "exit_page": None,
        "entry_url": None,
        "entry_title": None,
        "referrer": None,
        "referrer_host": None,
        "is_ai_referral": False,
        "ai_source": None,
        "utm": {},
        "browser": None,
        "browser_version": None,
        "os": None,
        "device_type": None,
        "country": None,
        "viewport_width": None,
        "viewport_height": None,
        "screen_width": None,
        "screen_height": None,
        "language": None,
        "timezone": None,
        "identity": {},
        "converted": False,
        "conversion_event": None,
        "converted_at": None,
        "last_event": None,
        "recording_bytes": 0,
        "recording_events": 0,
        "recording_chunks": 0,
        "recording_started_at": None,
        "recording_truncated": False,
    }


def apply_event(summary: dict[str, Any], event: CapturedEvent, config: HotspotConfig) -> None:
    props = event.properties
    name = event.event

    _extend_span(summary, event.timestamp, event.timestamp)
    summary["event_count"] += 1
    summary["last_event"] = name

    # A page view from a tab the browser has not rendered yet reports a 0 x 0 viewport, so zero
    # counts as unknown and a later event may fill the field in.
    for prop_key, field in _SESSION_PROPERTY_FIELDS.items():
        if summary.get(field) in (None, 0) and props.get(prop_key) not in (None, "", 0):
            summary[field] = props[prop_key]

    if summary.get("host") is None:
        host = props.get("$host")
        if not host and summary.get("entry_url"):
            try:
                host = urlsplit(summary["entry_url"]).hostname
            except Exception:
                host = None
        if host:
            summary["host"] = str(host).lower().split(":")[0]

    if summary.get("referrer") is None and props.get("$referrer"):
        referrer = str(props["$referrer"])
        summary["referrer"] = referrer
        summary["referrer_host"] = str(props.get("$referrer_host") or urlsplit(referrer).hostname or "") or None
        summary["is_ai_referral"] = bool(props.get("$is_ai_referral"))
        summary["ai_source"] = props.get("$ai_source") or None
    if not summary.get("utm"):
        utm = {key: value for key, value in props.items() if key.startswith("utm_") and isinstance(value, str)}
        if utm:
            summary["utm"] = utm

    if name == "$pageview":
        summary["pageview_count"] += 1
        pathname = props.get("$pathname")
        if isinstance(pathname, str) and pathname:
            pages: list[str] = summary["pages"]
            if not pages or pages[-1] != pathname:
                if len(pages) < MAX_PAGES_PER_SESSION:
                    pages.append(pathname)
            summary["entry_page"] = pages[0]
            summary["exit_page"] = pages[-1]
        if summary.get("entry_url") is None:
            summary["entry_url"] = props.get("$current_url") or props.get("$href") or None
            summary["entry_title"] = props.get("$title") or None
    elif name == "$pageleave":
        depth = props.get("$scroll_depth_percent")
        if isinstance(depth, (int, float)) and not isinstance(depth, bool):
            depth_value = int(max(0, min(100, depth)))
            current_depth = summary.get("max_scroll_depth")
            summary["max_scroll_depth"] = depth_value if current_depth is None else max(current_depth, depth_value)
        seconds = props.get("$duration_seconds")
        if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and seconds > 0:
            summary["active_seconds"] += int(seconds)
    elif name == "$autocapture":
        summary["click_count"] += 1
        if props.get("$is_dead_click"):
            summary["dead_click_count"] += 1
    elif name == "$rageclick":
        summary["rage_click_count"] += 1
    elif name == "$form_submit":
        summary["form_submit_count"] += 1
    elif name == "$exception":
        summary["error_count"] += 1
    elif name in ("$identify", "$set"):
        _merge_identity(summary, props)
        identified = props.get("$identified_id")
        if name == "$identify" and isinstance(identified, str) and identified:
            summary["distinct_id"] = identified[:200]
    elif not name.startswith("$"):
        summary["custom_event_count"] += 1

    if name in config.conversion_events and not summary["converted"]:
        summary["converted"] = True
        summary["conversion_event"] = name
        summary["converted_at"] = event.timestamp


def _merge_identity(summary: dict[str, Any], props: Mapping[str, Any]) -> None:
    person = props.get("$set")
    if not isinstance(person, Mapping):
        return
    identity: dict[str, Any] = dict(summary.get("identity") or {})
    for field, candidates in _IDENTITY_KEYS.items():
        for candidate in candidates:
            value = person.get(candidate)
            if isinstance(value, str) and value.strip():
                identity[field] = value.strip()[:200]
                break
    summary["identity"] = identity


def build_store(config: HotspotConfig, redis_client: Any | None = None) -> SessionStore:
    """Prefer Redis when it answers; otherwise keep the process running on memory."""
    client = redis_client
    if client is None and config.redis_url:
        try:
            import redis  # noqa: PLC0415 — the standalone server may run without the redis package

            client = redis.Redis.from_url(config.redis_url, socket_timeout=2, socket_connect_timeout=2)
        except Exception as error:  # noqa: BLE001
            logger.warning("Hotspot could not create a Redis client (%s); using in-memory storage", error)
            client = None
    if client is not None:
        try:
            client.ping()
            return SessionStore(RedisBackend(client, config), config)
        except Exception as error:  # noqa: BLE001
            logger.warning("Hotspot could not reach Redis (%s); using in-memory storage", error)
    return SessionStore(MemoryBackend(config), config)
