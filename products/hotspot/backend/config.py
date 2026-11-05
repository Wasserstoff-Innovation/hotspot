"""Runtime configuration for Hotspot, read from environment variables.

This module uses the standard library only, because the same code runs inside the
standalone server image where the ``posthog`` package is not installed.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_SITE_URL = "http://localhost:8000"
DEFAULT_DEMO_API_KEY = "hs_demo"
DEFAULT_CONVERSION_EVENTS = ("$form_submit",)


def _csv(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _int(value: str | None, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(value)) if value else default
    except ValueError:
        return default


def _flag(value: str | None, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() not in ("0", "false", "no", "off")


@dataclass(frozen=True)
class HotspotConfig:
    """Every operator-facing setting, with the environment variable that sets it."""

    site_url: str
    api_keys: frozenset[str]
    dashboard_password: str | None
    secret_key: str
    redis_url: str | None
    conversion_events: frozenset[str]
    demo_enabled: bool
    max_events: int
    max_session_events: int
    session_ttl_seconds: int
    max_batch_size: int
    max_body_bytes: int
    replay_enabled: bool
    max_recording_bytes: int
    max_recording_body_bytes: int

    @property
    def api_key_for_pages(self) -> str:
        """The key the bundled demo site and install snippets use."""
        if self.api_keys:
            return sorted(self.api_keys)[0]
        return DEFAULT_DEMO_API_KEY

    @property
    def dashboard_protected(self) -> bool:
        return bool(self.dashboard_password)

    @property
    def sdk_url(self) -> str:
        return f"{self.site_url}/hotspot.js"


def load_config(env: Mapping[str, str] | None = None) -> HotspotConfig:
    source = os.environ if env is None else env
    site_url = (source.get("HOTSPOT_SITE_URL") or source.get("SITE_URL") or DEFAULT_SITE_URL).rstrip("/")
    dashboard_password = source.get("HOTSPOT_DASHBOARD_PASSWORD") or None
    # A random fallback keeps signing working for a single process; set HOTSPOT_SECRET_KEY so
    # dashboard logins survive restarts and are shared across workers.
    secret_key = source.get("HOTSPOT_SECRET_KEY") or source.get("SECRET_KEY") or secrets.token_urlsafe(32)
    conversion_events = _csv(source.get("HOTSPOT_CONVERSION_EVENTS")) or DEFAULT_CONVERSION_EVENTS
    return HotspotConfig(
        site_url=site_url,
        api_keys=frozenset(_csv(source.get("HOTSPOT_API_KEYS"))),
        dashboard_password=dashboard_password,
        secret_key=secret_key,
        redis_url=source.get("REDIS_URL") or None,
        conversion_events=frozenset(conversion_events),
        demo_enabled=_flag(source.get("HOTSPOT_DEMO_ENABLED"), default=True),
        max_events=_int(source.get("HOTSPOT_MAX_EVENTS"), 5000),
        max_session_events=_int(source.get("HOTSPOT_MAX_SESSION_EVENTS"), 2000),
        session_ttl_seconds=_int(source.get("HOTSPOT_SESSION_TTL_SECONDS"), 7 * 24 * 3600, minimum=60),
        max_batch_size=_int(source.get("HOTSPOT_MAX_BATCH_SIZE"), 500),
        max_body_bytes=_int(source.get("HOTSPOT_MAX_BODY_BYTES"), 1024 * 1024, minimum=1024),
        replay_enabled=_flag(source.get("HOTSPOT_REPLAY_ENABLED"), default=True),
        # Compressed bytes of DOM recording kept per session; a typical minute of browsing is well under 1 MiB.
        max_recording_bytes=_int(source.get("HOTSPOT_MAX_RECORDING_BYTES"), 10 * 1024 * 1024, minimum=1024),
        max_recording_body_bytes=_int(source.get("HOTSPOT_MAX_RECORDING_BODY_BYTES"), 8 * 1024 * 1024, minimum=1024),
    )
