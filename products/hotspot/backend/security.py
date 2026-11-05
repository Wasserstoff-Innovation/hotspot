"""Response headers shared by both servers: content security policy, CORS for capture, caching."""

from __future__ import annotations

import secrets
from collections.abc import Mapping

FONT_STYLE_ORIGIN = "https://fonts.googleapis.com"
FONT_FILE_ORIGIN = "https://fonts.gstatic.com"

# The capture endpoint is called from every site that embeds the script, so it must answer any origin.
CAPTURE_CORS_HEADERS: dict[str, str] = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400",
}

NO_STORE_HEADERS: dict[str, str] = {"Cache-Control": "no-store"}

API_CSP = "default-src 'none'; frame-ancestors 'none'"


def generate_nonce() -> str:
    return secrets.token_urlsafe(16)


def page_csp(nonce: str, allow_page_frames: bool = False) -> str:
    """Inline scripts carry the nonce; inline styles stay allowed because the pages set them."""
    frame_src = "'self' https: http:" if allow_page_frames else "'self'"
    return "; ".join(
        [
            "default-src 'self'",
            f"script-src 'self' 'nonce-{nonce}'",
            f"style-src 'self' 'unsafe-inline' {FONT_STYLE_ORIGIN}",
            f"font-src 'self' data: {FONT_FILE_ORIGIN}",
            "img-src 'self' data: blob:",
            "connect-src 'self'",
            f"frame-src {frame_src}",
            "frame-ancestors 'self'",
            "base-uri 'self'",
            "form-action 'self'",
            "object-src 'none'",
        ]
    )


def is_same_site_form_post(headers: Mapping[str, str]) -> bool:
    """Browsers send Sec-Fetch-Site; a cross-site value means another site posted the form."""
    lowered = {key.lower(): value for key, value in headers.items()}
    site = lowered.get("sec-fetch-site")
    return site is None or site in ("same-origin", "same-site", "none")
