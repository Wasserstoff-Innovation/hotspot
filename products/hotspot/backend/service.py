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
            events = parse_batch(
                body,
                RequestContext.from_headers(headers),
                self.config,
                moment,
                extra_valid_keys=self.store.all_api_keys(),
            )
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
            chunk = parse_recording(
                body,
                params.get("compression") or None,
                self.config,
                moment,
                extra_valid_keys=self.store.all_api_keys(),
            )
        except IngestionError as error:
            return error.status, {"status": 0, "error": str(error)}
        stored = self.store.record_recording(chunk, now=moment)
        return 200, {"status": 1, "received": chunk.event_count, "stored": stored}

    def list_sites(self, authorized_scope: str = "*") -> list[dict[str, Any]]:
        from products.hotspot.backend.analytics import site_in_scope  # noqa: PLC0415

        all_sites = self.store.sites()
        return [s for s in all_sites if site_in_scope(s, authorized_scope)]

    def create_site(
        self,
        name: str,
        domain: str,
        confirmation_key: str | None = None,
        secret_key_override: str | None = None,
    ) -> tuple[bool, dict[str, Any] | str]:
        ok, res = self.store.create_site_safely(
            name,
            domain,
            confirmation_key,
            secret_key_override=secret_key_override,
        )
        if ok and isinstance(res, dict):
            res["snippet"] = (
                f'<script src="{self.config.sdk_url}" data-api-host="{self.config.site_url}" '
                f'data-api-key="{res.get("api_key")}" defer></script>'
            )
        return ok, res

    def delete_site(self, site_id: str) -> bool:
        return self.store.delete_site(site_id)

    def overview(
        self,
        params: Mapping[str, str],
        authorized_scope: str = "*",
        now: float | None = None,
    ) -> dict[str, Any]:
        moment = time.time() if now is None else now
        query = OverviewQuery.from_params(params)
        return build_overview(
            self.store,
            query,
            self.config,
            moment,
            authorized_scope=authorized_scope,
        )

    def session_detail(
        self,
        session_id: str,
        authorized_scope: str = "*",
        now: float | None = None,
    ) -> dict[str, Any] | None:
        moment = time.time() if now is None else now
        detail = self.store.session(session_id)
        if detail is None:
            return None
        if authorized_scope != "*":
            from products.hotspot.backend.analytics import record_in_scope  # noqa: PLC0415

            allowed_sites = self.list_sites(authorized_scope=authorized_scope)
            if not record_in_scope(
                detail.get("host"),
                detail.get("project_id"),
                authorized_scope,
                allowed_sites,
            ):
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

    def recording(
        self,
        session_id: str,
        params: Mapping[str, str],
        authorized_scope: str = "*",
    ) -> str | None:
        """The recording as JSON text. Chunks are spliced in as stored, so megabytes of events are not re-parsed."""
        summary = self.store.summary(session_id)
        if summary is None:
            return None
        if authorized_scope != "*":
            from products.hotspot.backend.analytics import record_in_scope  # noqa: PLC0415

            allowed_sites = self.list_sites(authorized_scope=authorized_scope)
            if not record_in_scope(
                summary.get("host"),
                summary.get("project_id"),
                authorized_scope,
                allowed_sites,
            ):
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

    def events_feed(
        self,
        params: Mapping[str, str],
        authorized_scope: str = "*",
        now: float | None = None,
    ) -> dict[str, Any]:
        moment = time.time() if now is None else now
        since = _float_param(params.get("since"))
        limit = _int_param(params.get("limit"), default=100, low=1, high=MAX_FEED_EVENTS)
        events = self.store.recent_events(limit=limit, since=since)
        if authorized_scope != "*":
            from products.hotspot.backend.analytics import record_in_scope  # noqa: PLC0415

            allowed_sites = self.list_sites(authorized_scope=authorized_scope)
            events = [
                e
                for e in events
                if record_in_scope(
                    (e.get("properties") or {}).get("$host"),
                    e.get("project_id"),
                    authorized_scope,
                    allowed_sites,
                )
            ]
        requested_site = (params.get("site") or "").strip()
        if requested_site and requested_site != "all":
            from products.hotspot.backend.analytics import record_in_scope  # noqa: PLC0415

            requested_sites = [
                s
                for s in self.list_sites(authorized_scope=authorized_scope)
                if requested_site.lower()
                in {
                    str(s.get("domain") or "").lower(),
                    str(s.get("id") or "").lower(),
                    str(s.get("api_key") or "").lower(),
                }
            ]
            events = [
                e
                for e in events
                if record_in_scope(
                    (e.get("properties") or {}).get("$host"),
                    e.get("project_id"),
                    requested_site,
                    requested_sites,
                )
            ]
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

    def login(
        self,
        secret_or_password: str | None,
        domain: str | None = None,
        now: float | None = None,
    ) -> str | None:
        moment = time.time() if now is None else now
        candidate = (secret_or_password or "").strip()
        domain_clean = (
            domain.strip().lower().removeprefix("http://").removeprefix("https://").split("/")[0].split(":")[0]
            if domain
            else ""
        )

        # 1. Master admin password check
        if password_matches(self.config.dashboard_password, candidate):
            return make_dashboard_token(self.config.secret_key, moment, scope="*")

        # 2. Domain + Secret Key sign-in
        if domain_clean and candidate:
            site = self.store.authenticate_site(domain_clean, candidate)
            if site:
                shared = self.store.get_sites_by_secret_key(candidate)
                domains = self._unique_site_domains(shared)
                scope_value = ",".join(domains) if domains else str(site["domain"])
                return make_dashboard_token(self.config.secret_key, moment, scope=scope_value)

        # 3. Direct Secret Key sign-in (domain omitted)
        if candidate and not domain_clean:
            shared = self.store.get_sites_by_secret_key(candidate)
            domains = self._unique_site_domains(shared)
            if domains:
                return make_dashboard_token(self.config.secret_key, moment, scope=",".join(domains))

        # 4. Fallback for unconfigured dev setups
        if not self.config.dashboard_protected and candidate in ("admin", "guest", "demo", "buxa2026!"):
            return make_dashboard_token(self.config.secret_key, moment, scope="*")

        return None

    @staticmethod
    def _unique_site_domains(sites: list[dict[str, Any]]) -> list[str]:
        seen: set[str] = set()
        domains: list[str] = []
        for site in sites:
            domain = str(site.get("domain") or site.get("id") or "").strip()
            if not domain:
                continue
            key = domain.lower()
            if key not in seen:
                seen.add(key)
                domains.append(domain)
        return domains

    def explicit_authorized_scope(
        self,
        cookie_token: str | None,
        authorization: str | None,
        now: float | None = None,
    ) -> tuple[bool, str | None]:
        """Verify only an explicit session cookie or Bearer credential (never the open-dev fallback)."""
        moment = time.time() if now is None else now
        if cookie_token:
            scope = verify_dashboard_token(self.config.secret_key, cookie_token, moment)
            if scope:
                return True, scope
        scheme, _, credential = (authorization or "").partition(" ")
        if scheme.lower() == "bearer" and credential:
            cred = credential.strip()
            if self.config.dashboard_protected and password_matches(self.config.dashboard_password, cred):
                return True, "*"
            shared = self.store.get_sites_by_secret_key(cred)
            domains = self._unique_site_domains(shared)
            if domains:
                return True, ",".join(domains)
        return False, None

    def resolve_owner_secret_key(
        self,
        authorized_scope: str,
        preferred_site: str | None = None,
    ) -> str | None:
        owned = self.list_sites(authorized_scope=authorized_scope)
        if not owned:
            return None
        preferred = (preferred_site or "").strip().lower()
        if preferred and preferred != "all":
            for site in owned:
                candidates = {
                    str(site.get("domain") or "").strip().lower(),
                    str(site.get("id") or "").strip().lower(),
                    str(site.get("api_key") or "").strip().lower(),
                }
                if preferred in candidates:
                    sec = str(site.get("secret_key") or "").strip()
                    return sec or None
            return None
        for site in owned:
            sec = str(site.get("secret_key") or "").strip()
            if sec:
                return sec
        return None

    def create_site_for_session(
        self,
        name: str,
        domain: str,
        *,
        confirmation_key: str | None = None,
        key_mode: str = "new",
        source_site: str | None = None,
        cookie_token: str | None = None,
        authorization: str | None = None,
        now: float | None = None,
    ) -> tuple[int, dict[str, Any]]:
        from products.hotspot.backend.analytics import merge_scopes  # noqa: PLC0415

        domain_clean = domain.strip()
        if not domain_clean:
            return 400, {"status": 0, "error": "Website domain or URL is required."}

        mode = (key_mode or "new").strip().lower()
        if mode not in {"new", "same"}:
            return 400, {"status": 0, "error": "Invalid key_mode. Choose 'same' or 'new'."}

        moment = time.time() if now is None else now
        has_explicit_auth, current_scope = self.explicit_authorized_scope(cookie_token, authorization, now=moment)

        secret_key_override: str | None = None
        if mode == "same":
            if not has_explicit_auth or not current_scope:
                return 401, {
                    "status": 0,
                    "error": "Sign in to your dashboard or existing site before reusing a secret key.",
                }
            preferred = (source_site or "").strip()
            secret_key_override = self.resolve_owner_secret_key(current_scope, preferred_site=preferred or None)
            if not secret_key_override:
                if preferred and preferred.lower() != "all":
                    return 403, {"status": 0, "error": "Selected source site does not belong to your current session."}
                return 400, {
                    "status": 0,
                    "error": "No existing site secret key is available to share. Create a new key instead.",
                }

        ok, res = self.create_site(
            name.strip() or domain_clean,
            domain_clean,
            confirmation_key=(confirmation_key or "").strip() or None,
            secret_key_override=secret_key_override,
        )
        if not ok:
            status_code = 409 if "already registered" in str(res) else 400
            payload: dict[str, Any] = {"status": 0, "error": str(res)}
            if status_code == 409:
                payload["requires_confirmation"] = True
            return status_code, payload

        assert isinstance(res, dict)
        new_scope = (
            merge_scopes(current_scope, str(res["domain"]))
            if has_explicit_auth and current_scope
            else str(res["domain"])
        )
        token = make_dashboard_token(self.config.secret_key, moment, scope=new_scope)
        return 200, {
            "status": 1,
            "site": res,
            "token": token,
            "scope": new_scope,
            "key_mode": mode,
        }

    def delete_site_for_session(
        self,
        identifier: str,
        authorized_scope: str,
        *,
        now: float | None = None,
    ) -> tuple[int, dict[str, Any], str | None]:
        from products.hotspot.backend.analytics import remove_from_scope, site_in_scope  # noqa: PLC0415

        target = identifier.strip()
        target_site = None
        for site in self.store.sites():
            if target.lower() in {
                str(site.get("id") or "").lower(),
                str(site.get("domain") or "").lower(),
            }:
                target_site = site
                break
        if target_site is None:
            return 404, {"status": 0, "error": "site not found"}, None
        if authorized_scope != "*" and not site_in_scope(target_site, authorized_scope):
            return 403, {"status": 0, "error": "Unauthorized to delete this site"}, None
        if not self.delete_site(identifier):
            return 404, {"status": 0, "error": "site not found"}, None

        refreshed_token: str | None = None
        updated_scope: str = authorized_scope
        if authorized_scope != "*":
            moment = time.time() if now is None else now
            updated_scope = remove_from_scope(authorized_scope, str(target_site.get("domain") or target))
            refreshed_token = (
                make_dashboard_token(self.config.secret_key, moment, scope=updated_scope) if updated_scope else ""
            )
        return 200, {"status": 1, "deleted": identifier, "scope": updated_scope}, refreshed_token

    def authorized_scope(
        self,
        cookie_token: str | None,
        authorization: str | None,
        now: float | None = None,
    ) -> tuple[bool, str | None]:
        ok, scope = self.explicit_authorized_scope(cookie_token, authorization, now=now)
        if ok:
            return True, scope
        if not self.config.dashboard_protected:
            return True, "*"
        return False, None

    def is_authorized(
        self,
        cookie_token: str | None,
        authorization: str | None,
        now: float | None = None,
    ) -> bool:
        ok, _ = self.authorized_scope(cookie_token, authorization, now=now)
        return ok

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
            f"- MCP Streamable HTTP endpoint: {self.config.site_url}/mcp\n"
            f"- MCP Legacy SSE endpoint: {self.config.site_url}/mcp/sse\n"
            "- MCP Auth: pass your site's private Secret Key (hs_sec_...) via `Authorization: Bearer hs_sec_...`\n"
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
