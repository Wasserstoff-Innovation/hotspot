"""Dashboard sign-in: one shared password, exchanged for a signed, expiring cookie token."""

from __future__ import annotations

import hmac
import hashlib

DASHBOARD_COOKIE = "hotspot_dashboard"
TOKEN_TTL_SECONDS = 7 * 24 * 3600


def password_matches(expected: str | None, candidate: str | None) -> bool:
    if not expected or candidate is None:
        return False
    return hmac.compare_digest(expected.encode("utf-8"), candidate.encode("utf-8"))


def make_dashboard_token(secret: str, now: float, ttl: int = TOKEN_TTL_SECONDS) -> str:
    expires = str(int(now + ttl))
    return f"{expires}.{_sign(secret, expires)}"


def verify_dashboard_token(secret: str, token: str | None, now: float) -> bool:
    if not token or "." not in token:
        return False
    expires, _, signature = token.partition(".")
    if not expires.isdigit() or int(expires) < now:
        return False
    return hmac.compare_digest(_sign(secret, expires), signature)


def _sign(secret: str, message: str) -> str:
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
