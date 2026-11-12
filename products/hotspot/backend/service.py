"""The framework-neutral application layer. Django views and the standalone server both call it."""

from __future__ import annotations

import json
import time
import zlib
import logging
import secrets
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

from products.hotspot import __version__
from products.hotspot.backend.analytics import OverviewQuery, build_overview, format_session, setup_info
from products.hotspot.backend.auth import make_dashboard_token, password_matches, verify_dashboard_token
from products.hotspot.backend.config import HotspotConfig, load_config
from products.hotspot.backend.ingestion import IngestionError, RequestContext, parse_batch, parse_recording
from products.hotspot.backend.store import SessionStore, build_store

logger = logging.getLogger(__name__)

PRODUCT_PATH = Path(__file__).resolve().parent.parent
SDK_PATH = PRODUCT_PATH / "sdk" / "hotspot.js"
SKILL_PATH = PRODUCT_PATH / "skill" / "SKILL.md"
_CONNECT_TTL_SECONDS = 600
_CONNECT_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
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
    "connect": "Authorize AI Assistant or CLI - Hotspot",
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
        site: str | None = None,
    ) -> dict[str, Any] | None:
        moment = time.time() if now is None else now
        detail = self.store.session(session_id)
        if detail is None:
            return None
        requested_site = (site or "").strip()
        need_scope_check = authorized_scope != "*" or (requested_site and requested_site.lower() != "all")
        if need_scope_check:
            from products.hotspot.backend.analytics import (  # noqa: PLC0415
                record_in_scope,
                record_matches_site,
                site_in_scope,
            )

            all_sites = self.store.sites()
            if authorized_scope != "*":
                allowed_sites = [s for s in all_sites if site_in_scope(s, authorized_scope)]
                if not record_in_scope(
                    detail.get("host"),
                    detail.get("project_id"),
                    authorized_scope,
                    allowed_sites,
                    all_sites=all_sites,
                    url=detail.get("entry_url"),
                ):
                    return None
            if requested_site and requested_site.lower() != "all":
                if not record_matches_site(
                    detail.get("host"),
                    detail.get("project_id"),
                    requested_site,
                    all_sites,
                    url=detail.get("entry_url"),
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
        requested_site = (params.get("site") or "").strip()
        need_scope_check = authorized_scope != "*" or (requested_site and requested_site.lower() != "all")
        if need_scope_check:
            from products.hotspot.backend.analytics import (  # noqa: PLC0415
                record_in_scope,
                record_matches_site,
                site_in_scope,
            )

            all_sites = self.store.sites()
            if authorized_scope != "*":
                allowed_sites = [s for s in all_sites if site_in_scope(s, authorized_scope)]
                if not record_in_scope(
                    summary.get("host"),
                    summary.get("project_id"),
                    authorized_scope,
                    allowed_sites,
                    all_sites=all_sites,
                    url=summary.get("entry_url"),
                ):
                    return None
            if requested_site and requested_site.lower() != "all":
                if not record_matches_site(
                    summary.get("host"),
                    summary.get("project_id"),
                    requested_site,
                    all_sites,
                    url=summary.get("entry_url"),
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
        requested_site = (params.get("site") or "").strip()

        from products.hotspot.backend.analytics import (  # noqa: PLC0415
            record_in_scope,
            record_matches_site,
            scope_domains,
            site_in_scope,
        )

        authorized_domains = scope_domains(authorized_scope)
        if authorized_domains is not None and len(authorized_domains) == 0:
            events: list[dict[str, Any]] = []
        else:
            site_filter = requested_site if requested_site and requested_site.lower() != "all" else ""
            if not site_filter and authorized_domains is not None and len(authorized_domains) == 1:
                site_filter = authorized_domains[0]
            all_sites = self.store.sites()
            allowed_sites = (
                all_sites
                if authorized_domains is None
                else [s for s in all_sites if site_in_scope(s, authorized_scope)]
            )
            fetch_limit = self.config.max_events if (authorized_domains is not None or site_filter) else limit
            events = self.store.recent_events(limit=fetch_limit, since=since)
            if authorized_domains is not None or site_filter:
                filtered: list[dict[str, Any]] = []
                for e in events:
                    props = e.get("properties") or {}
                    ev_host = e.get("host") or props.get("$host")
                    ev_url = props.get("$current_url") or props.get("$href")
                    ev_pid = e.get("project_id")
                    if authorized_domains is not None and not record_in_scope(
                        ev_host,
                        ev_pid,
                        authorized_scope,
                        allowed_sites,
                        all_sites=all_sites,
                        url=ev_url,
                    ):
                        continue
                    if site_filter and not record_matches_site(
                        ev_host,
                        ev_pid,
                        site_filter,
                        all_sites,
                        url=ev_url,
                    ):
                        continue
                    filtered.append(e)
                    if len(filtered) >= limit:
                        break
                events = filtered
            else:
                events = events[:limit]
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
        base = self.config.site_url.rstrip("/")
        return (
            "# Hotspot\n"
            "> Self-hosted product telemetry: one script tag captures page views, clicks, rage clicks, "
            "dead clicks, scroll depth, form submits, JavaScript errors, web vitals and a DOM recording of "
            "each session. The dashboard replays sessions with console and network activity, and shows "
            "funnels, paths, friction, pages, referrers and errors.\n\n"
            f"- Skill file (downloadable Markdown for coding agents): {base}/skill.md\n"
            f"- Dashboard: {base}/dashboard/\n"
            f"- Browser agent login / connect page: {base}/connect/\n"
            f"- Sample site: {base}/demo/\n"
            f"- Script: {self.config.sdk_url}\n"
            f"- Capture endpoint: {base}/hotspot/batch/\n"
            f"- Recording endpoint: {base}/hotspot/replay/\n"
            f"- Register website API: POST {base}/hotspot/api/sites/\n"
            f"- Automated device connect API: POST {base}/hotspot/api/connect/start/ -> poll POST {base}/hotspot/api/connect/token/\n"
            f"- MCP Streamable HTTP endpoint: {base}/mcp\n"
            f"- MCP Legacy SSE endpoint: {base}/mcp/sse\n"
            "- MCP Auth: pass your site's private Secret Key (hs_sec_...) via `Authorization: Bearer hs_sec_...`\n"
            "- Local Secret Storage: save hs_sec_... only in gitignored local files (.env.hotspot.local or ~/.config/hotspot/credentials.json, chmod 600); never embed hs_sec_... in client JS/HTML.\n"
        )

    def skill_markdown(self) -> str:
        base = self.config.site_url.rstrip("/")
        template = SKILL_PATH.read_text(encoding="utf-8")
        return (
            template.replace("{{ site_url }}", base)
            .replace("{{ sdk_url }}", self.config.sdk_url)
            .replace("{{ mcp_url }}", f"{base}/mcp")
            .replace("{{ version }}", __version__)
        )

    @staticmethod
    def _normalize_user_code(raw: str | None) -> str:
        cleaned = "".join(ch for ch in (raw or "").upper() if ch.isalnum())
        if len(cleaned) != 8:
            return ""
        return f"{cleaned[:4]}-{cleaned[4:]}"

    @staticmethod
    def _connect_dev_key(device_code: str) -> str:
        return f"connect:dev:{device_code}"

    @staticmethod
    def _connect_usr_key(user_code: str) -> str:
        return f"connect:usr:{user_code}"

    def _public_site_summary(self, site: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "id": site.get("id"),
            "name": site.get("name"),
            "domain": site.get("domain"),
            "api_key": site.get("api_key"),
            "created_at": site.get("created_at"),
        }

    def start_connect_session(
        self,
        *,
        client_name: str = "AI Assistant",
        domain_hint: str = "",
        name_hint: str = "",
        redirect_uri: str = "",
        state: str = "",
        now: float | None = None,
    ) -> tuple[int, dict[str, Any]]:
        clean_redirect = (redirect_uri or "").strip()
        if clean_redirect and not is_safe_loopback_redirect(clean_redirect):
            return 400, {
                "status": 0,
                "error": "redirect_uri must be an http://localhost:<port>/... or http://127.0.0.1:<port>/... callback URL.",
            }
        moment = time.time() if now is None else now
        device_code = f"hs_dev_{secrets.token_urlsafe(24)}"
        user_code = ""
        for _ in range(8):
            raw_code = "".join(secrets.choice(_CONNECT_CODE_ALPHABET) for _ in range(8))
            candidate = f"{raw_code[:4]}-{raw_code[4:]}"
            if self.store.get_ephemeral(self._connect_usr_key(candidate)) is None:
                user_code = candidate
                break
        if not user_code:
            return 503, {"status": 0, "error": "Could not allocate connect code. Retry shortly."}

        clean_client = " ".join((client_name or "AI Assistant").split())[:64] or "AI Assistant"
        record = {
            "device_code": device_code,
            "user_code": user_code,
            "client_name": clean_client,
            "domain_hint": domain_hint.strip()[:200],
            "name_hint": name_hint.strip()[:100],
            "redirect_uri": clean_redirect,
            "state": state.strip()[:200],
            "status": "pending",
            "created_at": moment,
            "expires_at": moment + _CONNECT_TTL_SECONDS,
        }
        self.store.set_ephemeral(self._connect_dev_key(device_code), record, _CONNECT_TTL_SECONDS)
        self.store.set_ephemeral(self._connect_usr_key(user_code), {"device_code": device_code}, _CONNECT_TTL_SECONDS)

        base = self.config.site_url.rstrip("/")
        return 200, {
            "status": 1,
            "device_code": device_code,
            "user_code": user_code,
            "verification_uri": f"{base}/connect/?code={user_code}",
            "verification_uri_base": f"{base}/connect/",
            "expires_in": _CONNECT_TTL_SECONDS,
            "interval": 2,
        }

    def connect_session_status(
        self,
        user_code: str,
        *,
        cookie_token: str | None = None,
        authorization: str | None = None,
        now: float | None = None,
    ) -> tuple[int, dict[str, Any]]:
        moment = time.time() if now is None else now
        has_auth, scope = self.explicit_authorized_scope(cookie_token, authorization, now=moment)
        if not has_auth and not self.config.dashboard_protected:
            has_auth, scope = True, "*"
        signed_sites = (
            [self._public_site_summary(s) for s in self.list_sites(authorized_scope=scope or "*")]
            if has_auth and scope
            else []
        )

        norm = self._normalize_user_code(user_code)
        session_info: dict[str, Any] | None = None
        if norm:
            ptr = self.store.get_ephemeral(self._connect_usr_key(norm))
            record = self.store.get_ephemeral(self._connect_dev_key(str(ptr["device_code"]))) if ptr else None
            if record and float(record.get("expires_at") or 0) > moment:
                session_info = {
                    "user_code": record["user_code"],
                    "client_name": record.get("client_name") or "AI Assistant",
                    "domain_hint": record.get("domain_hint") or "",
                    "name_hint": record.get("name_hint") or "",
                    "has_redirect_uri": bool(record.get("redirect_uri")),
                    "status": record.get("status") or "pending",
                    "expires_in": max(0, int(float(record["expires_at"]) - moment)),
                }

        return 200, {
            "status": 1,
            "session": session_info,
            "signed_in": bool(has_auth),
            "is_admin": scope == "*",
            "dashboard_protected": self.config.dashboard_protected,
            "sites": signed_sites,
        }

    def _resolve_approved_sites_and_token(
        self,
        *,
        mode: str,
        site_id: str | None,
        domain: str,
        name: str,
        secret_key: str,
        password: str,
        confirmation_key: str | None,
        key_mode: str,
        source_site: str | None,
        cookie_token: str | None,
        authorization: str | None,
        moment: float,
    ) -> tuple[int, dict[str, Any], str | None, dict[str, Any] | None, str | None]:
        """Resolve (status_code, error_body, grant_secret, primary_site, refreshed_cookie_token)."""
        from products.hotspot.backend.analytics import site_in_scope  # noqa: PLC0415

        clean_mode = (mode or "existing").strip().lower()
        if clean_mode == "register":
            status_code, body = self.create_site_for_session(
                name,
                domain,
                confirmation_key=confirmation_key,
                key_mode=key_mode,
                source_site=source_site,
                cookie_token=cookie_token,
                authorization=authorization,
                now=moment,
            )
            if status_code != 200:
                return status_code, body, None, None, None
            created = body["site"]
            return 200, {}, str(created["secret_key"]), created, str(body.get("token") or "")

        if clean_mode == "login":
            cred = (password or secret_key or "").strip()
            token = self.login(cred, domain or None, now=moment)
            if token is None:
                return 401, {"status": 0, "error": "Invalid domain or secret key. Try again."}, None, None, None
            login_scope = verify_dashboard_token(self.config.secret_key, token, moment) or "*"
            owned = self.list_sites(authorized_scope=login_scope)
            chosen: dict[str, Any] | None = None
            target = (site_id or domain or "").strip().lower()
            if target:
                for site in owned:
                    if target in {str(site.get("id") or "").lower(), str(site.get("domain") or "").lower()}:
                        chosen = site
                        break
            elif owned:
                chosen = owned[0]
            if login_scope == "*" and password_matches(self.config.dashboard_password, cred):
                grant_secret = str(chosen["secret_key"]) if chosen and chosen.get("secret_key") else cred
                return 200, {}, grant_secret, chosen, token
            return 200, {}, cred, chosen, token

        if clean_mode == "existing":
            has_auth, scope = self.explicit_authorized_scope(cookie_token, authorization, now=moment)
            if not has_auth and not self.config.dashboard_protected:
                has_auth, scope = True, "*"
            if not has_auth or not scope:
                return 401, {"status": 0, "error": "Sign in before authorizing an existing site."}, None, None, None

            owned = self.list_sites(authorized_scope=scope)
            target = (site_id or domain or "").strip().lower()
            chosen_site: dict[str, Any] | None = None
            if target and target != "__admin__":
                for site in owned:
                    if target in {str(site.get("id") or "").lower(), str(site.get("domain") or "").lower()}:
                        chosen_site = site
                        break
                if chosen_site is None or (scope != "*" and not site_in_scope(chosen_site, scope)):
                    return 403, {"status": 0, "error": "Selected site is outside your session scope."}, None, None, None
            elif target != "__admin__" and owned:
                chosen_site = owned[0]

            if chosen_site and chosen_site.get("secret_key"):
                return 200, {}, str(chosen_site["secret_key"]), chosen_site, None
            if scope == "*" and self.config.dashboard_protected and self.config.dashboard_password:
                return 200, {}, self.config.dashboard_password, chosen_site, None
            return 400, {"status": 0, "error": "No secret key found for the selected site."}, None, None, None

        return 400, {"status": 0, "error": "Invalid approval mode."}, None, None, None

    def approve_connect_session(
        self,
        user_code: str,
        *,
        mode: str = "existing",
        site_id: str | None = None,
        domain: str = "",
        name: str = "",
        secret_key: str = "",
        password: str = "",
        confirmation_key: str | None = None,
        key_mode: str = "new",
        source_site: str | None = None,
        cookie_token: str | None = None,
        authorization: str | None = None,
        now: float | None = None,
    ) -> tuple[int, dict[str, Any]]:
        moment = time.time() if now is None else now
        norm = self._normalize_user_code(user_code)
        if not norm:
            return 400, {"status": 0, "error": "Enter a valid 8-character connect code (e.g. ABCD-1234)."}

        ptr = self.store.get_ephemeral(self._connect_usr_key(norm))
        device_code = str(ptr.get("device_code") or "") if ptr else ""
        record = self.store.get_ephemeral(self._connect_dev_key(device_code)) if device_code else None
        if not record or float(record.get("expires_at") or 0) <= moment:
            return 404, {
                "status": 0,
                "error": "Connect code is invalid or expired. Generate a new one from your CLI or agent.",
            }
        if record.get("status") != "pending":
            return 409, {"status": 0, "error": "This connect code has already been authorized."}

        err_status, err_body, grant_secret, primary_site, refreshed_token = self._resolve_approved_sites_and_token(
            mode=mode,
            site_id=site_id,
            domain=domain,
            name=name,
            secret_key=secret_key,
            password=password,
            confirmation_key=confirmation_key,
            key_mode=key_mode,
            source_site=source_site,
            cookie_token=cookie_token,
            authorization=authorization,
            moment=moment,
        )
        if err_status != 200 or not grant_secret:
            return err_status, err_body

        matching_sites = self.store.get_sites_by_secret_key(grant_secret)
        if primary_site and all(s.get("id") != primary_site.get("id") for s in matching_sites):
            matching_sites = [primary_site, *matching_sites]
        if not matching_sites and primary_site is None:
            matching_sites = self.store.sites()

        public_sites = [self._public_site_summary(s) for s in matching_sites]
        chosen_summary = (
            self._public_site_summary(primary_site) if primary_site else (public_sites[0] if public_sites else None)
        )
        primary_domain = (
            str(chosen_summary.get("domain") or "")
            if chosen_summary
            else (self._unique_site_domains(matching_sites)[0] if matching_sites else "*")
        )
        primary_api_key = str((chosen_summary or {}).get("api_key") or self.config.api_key_for_pages)

        updated_record = dict(record)
        updated_record.update(
            {
                "status": "approved",
                "approved_at": moment,
                "grant": {
                    "domain": primary_domain,
                    "domains": self._unique_site_domains(matching_sites)
                    or ([primary_domain] if primary_domain != "*" else []),
                    "site": chosen_summary,
                    "sites": public_sites,
                    "api_key": primary_api_key,
                    "secret_key": grant_secret,
                },
            }
        )
        remaining_ttl = max(180, int(float(record["expires_at"]) - moment))
        self.store.set_ephemeral(self._connect_dev_key(device_code), updated_record, remaining_ttl)
        self.store.set_ephemeral(self._connect_usr_key(norm), {"device_code": device_code}, remaining_ttl)

        redirect_to = _build_loopback_callback_url(
            str(record.get("redirect_uri") or ""),
            device_code=device_code,
            state=str(record.get("state") or ""),
        )
        response_payload: dict[str, Any] = {
            "status": 1,
            "approved": True,
            "user_code": norm,
            "client_name": record.get("client_name") or "AI Assistant",
            "domain": primary_domain,
            "domains": updated_record["grant"]["domains"],
            "site": chosen_summary,
            "sites": public_sites,
            "redirect_to": redirect_to,
        }
        if mode.strip().lower() == "register" and primary_site:
            response_payload["created_site"] = dict(primary_site)
        if refreshed_token:
            response_payload["token"] = refreshed_token
        return 200, response_payload

    def exchange_connect_token(self, device_code: str, *, now: float | None = None) -> tuple[int, dict[str, Any]]:
        moment = time.time() if now is None else now
        code = (device_code or "").strip()
        if not code:
            return 400, {"status": 0, "error": "device_code is required."}
        dev_key = self._connect_dev_key(code)
        record = self.store.get_ephemeral(dev_key)
        if not record or float(record.get("expires_at") or 0) <= moment:
            return 404, {"status": 0, "error": "expired_or_invalid_code"}
        if record.get("status") != "approved":
            return 202, {
                "status": 0,
                "pending": True,
                "error": "authorization_pending",
                "expires_in": max(0, int(float(record["expires_at"]) - moment)),
            }

        consumed = self.store.pop_ephemeral(dev_key)
        if not consumed:
            return 404, {"status": 0, "error": "code_already_used"}
        norm_usr = self._normalize_user_code(str(consumed.get("user_code") or ""))
        if norm_usr:
            self.store.pop_ephemeral(self._connect_usr_key(norm_usr))

        grant = dict(consumed.get("grant") or {})
        base = self.config.site_url.rstrip("/")
        secret_key = str(grant.get("secret_key") or "")
        api_key = str(grant.get("api_key") or "")
        domain = str(grant.get("domain") or "")
        domains = list(grant.get("domains") or [])
        mcp_config = {
            "mcpServers": {
                "hotspot": {
                    "type": "http",
                    "url": f"{base}/mcp",
                    "headers": {"Authorization": f"Bearer {secret_key}"},
                }
            }
        }
        env_lines = (
            f"HOTSPOT_HOST={base}\n"
            f"HOTSPOT_DOMAIN={domain}\n"
            f"HOTSPOT_API_KEY={api_key}\n"
            f"HOTSPOT_SECRET_KEY={secret_key}\n"
        )
        credentials_json = {
            "site_url": base,
            "sdk_url": self.config.sdk_url,
            "mcp_url": f"{base}/mcp",
            "mcp_sse_url": f"{base}/mcp/sse",
            "skill_url": f"{base}/skill.md",
            "domain": domain,
            "domains": domains,
            "api_key": api_key,
            "secret_key": secret_key,
        }
        return 200, {
            "status": 1,
            "authorized": True,
            "client_name": consumed.get("client_name") or "AI Assistant",
            **grant,
            "site_url": base,
            "sdk_url": self.config.sdk_url,
            "mcp_url": f"{base}/mcp",
            "mcp_sse_url": f"{base}/mcp/sse",
            "skill_url": f"{base}/skill.md",
            "mcp_config": mcp_config,
            "env_lines": env_lines,
            "credentials_json": credentials_json,
        }


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


def is_safe_loopback_redirect(uri: str) -> bool:
    """Only allow explicit http://127.0.0.1:<port>/... or http://localhost:<port>/... loopback callbacks."""
    candidate = (uri or "").strip()
    if not candidate or any(ch.isspace() or ch == "\\" for ch in candidate):
        return False
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return False
    if parsed.scheme.lower() != "http":
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    host = (parsed.hostname or "").lower()
    if host not in {"127.0.0.1", "localhost"}:
        return False
    try:
        port = parsed.port
    except ValueError:
        return False
    return port is not None and 1 <= port <= 65535


def _build_loopback_callback_url(redirect_uri: str, *, device_code: str, state: str = "") -> str | None:
    if not redirect_uri or not is_safe_loopback_redirect(redirect_uri):
        return None
    parsed = urlsplit(redirect_uri)
    params: list[tuple[str, str]] = [("code", device_code)]
    if state:
        params.append(("state", state))
    existing_query = f"{parsed.query}&" if parsed.query else ""
    new_query = f"{existing_query}{urlencode(params)}"
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, new_query, ""))
