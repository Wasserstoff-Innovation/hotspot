"""Model Context Protocol (MCP) server engine for Hotspot.

Provides full Streamable HTTP, Legacy SSE, Django HTTP, and stdio support with strict
secret-key tenant isolation, framework setup guides, synthetic session testing, and
chart-ready analytics tools. Uses only the Python standard library + Hotspot services
so it runs cleanly in both the minimal Alpine standalone container and Django.
"""

from __future__ import annotations

import sys
import hmac
import json
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TextIO

from products.hotspot import __version__
from products.hotspot.backend.auth import password_matches
from products.hotspot.backend.service import HotspotService, get_service

SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_PROTOCOL_VERSION = "2025-03-26"

_ERROR_PARSE = -32700
_ERROR_INVALID_REQUEST = -32600
_ERROR_METHOD_NOT_FOUND = -32601
_ERROR_INVALID_PARAMS = -32602
_ERROR_INTERNAL = -32603
_ERROR_UNAUTHORIZED = -32001

VALID_FRAMEWORKS = ("html", "nextjs_app", "nextjs_pages", "react_vite", "sveltekit", "astro", "django")
VALID_CHART_TYPES = (
    "all",
    "trend_timeseries",
    "conversion_funnel",
    "top_pages",
    "traffic_channels",
    "device_browser_breakdown",
    "realtime_activity",
)


class McpAuthError(PermissionError):
    """Raised when an explicit MCP credential is missing or rejected."""


class McpProtocolError(Exception):
    """JSON-RPC 2.0 protocol exception."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


@dataclass(frozen=True, kw_only=True)
class McpAuthContext:
    """Resolved tenant isolation context for an MCP request."""

    scope: str
    site: dict[str, Any] | None = None
    is_admin: bool = False
    secret_key_hint: str | None = None


def _mask_key(key: str | None) -> str | None:
    if not key:
        return None
    if len(key) <= 10:
        return "***"
    return f"{key[:6]}...{key[-4:]}"


def _iso_utc(timestamp: float | int | None) -> str | None:
    if timestamp is None:
        return None
    try:
        return datetime.fromtimestamp(float(timestamp), tz=UTC).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _snippet_for(service: HotspotService, api_key: str) -> str:
    return (
        f'<script src="{service.config.sdk_url}" '
        f'data-api-host="{service.config.site_url}" '
        f'data-api-key="{api_key}" defer></script>'
    )


def _mcp_config_snippet(service: HotspotService, secret_key: str | None = None) -> dict[str, Any]:
    token_placeholder = secret_key or "<YOUR_HOTSPOT_SECRET_KEY>"
    base = service.config.site_url.rstrip("/")
    return {
        "mcpServers": {
            "hotspot": {
                "type": "http",
                "url": f"{base}/mcp",
                "headers": {
                    "Authorization": f"Bearer {token_placeholder}",
                },
            }
        }
    }


def _framework_guide(service: HotspotService, framework: str, api_key: str, domain: str) -> dict[str, Any]:
    sdk_url = service.config.sdk_url
    site_url = service.config.site_url
    raw_script = _snippet_for(service, api_key)

    guides: dict[str, dict[str, Any]] = {
        "html": {
            "framework": "html",
            "target_file": "index.html (or base layout before </head>)",
            "code": raw_script,
            "notes": [
                "Use ONLY the public api_key (hs_live_...) in frontend code. Never place hs_sec_... in HTML or JS bundles.",
                "Automatic tracking includes pageviews, SPA history navigation, clicks, rage clicks, dead clicks, form submits, JS errors, web vitals, and session replay.",
            ],
        },
        "nextjs_app": {
            "framework": "nextjs_app",
            "target_file": "app/layout.tsx",
            "code": (
                "import Script from 'next/script';\n\n"
                "// Inside RootLayout's returned <html>:\n"
                "<Script\n"
                f'  src="{sdk_url}"\n'
                f'  data-api-host="{site_url}"\n'
                f'  data-api-key="{api_key}"\n'
                '  strategy="afterInteractive"\n'
                "/>"
            ),
            "notes": [
                "Place <Script> inside <body> or <head> of app/layout.tsx. Hotspot automatically tracks App Router soft navigations via history.pushState.",
                "In TypeScript files calling window.hotspot, add a global Window interface declaration if needed.",
            ],
        },
        "nextjs_pages": {
            "framework": "nextjs_pages",
            "target_file": "pages/_app.tsx",
            "code": (
                "import Script from 'next/script';\n\n"
                "// Inside App return JSX:\n"
                "<Script\n"
                f'  src="{sdk_url}"\n'
                f'  data-api-host="{site_url}"\n'
                f'  data-api-key="{api_key}"\n'
                '  strategy="afterInteractive"\n'
                "/>"
            ),
            "notes": [
                "Put the public key in NEXT_PUBLIC_HOTSPOT_API_KEY if using environment variables.",
                "Never store HOTSPOT_SECRET_KEY under NEXT_PUBLIC_*.",
            ],
        },
        "react_vite": {
            "framework": "react_vite",
            "target_file": "index.html",
            "code": (
                "<!-- Inside <head> of index.html -->\n"
                f"{raw_script}\n\n"
                "// Optional React helper hook (e.g., on user login):\n"
                "// window.hotspot?.identify(user.id, { email: user.email, plan: user.plan });\n"
                "// window.hotspot?.capture('checkout_completed', { amount: 49 });"
            ),
            "notes": [
                "Vite injects script tags cleanly in index.html without extra npm dependencies.",
                "Hotspot detects client-side React Router changes automatically.",
            ],
        },
        "sveltekit": {
            "framework": "sveltekit",
            "target_file": "src/app.html",
            "code": (f"<!-- Inside <head> of src/app.html before %sveltekit.head% -->\n{raw_script}"),
            "notes": [
                "Only %sveltekit.body% swaps on client navigation, so placing the tag in src/app.html tracks all routes.",
            ],
        },
        "astro": {
            "framework": "astro",
            "target_file": "src/layouts/Layout.astro",
            "code": (f"<!-- Inside <head> of your shared Astro layout -->\n{raw_script}"),
            "notes": [
                "Works with both MPA pages and Astro <ViewTransitions />.",
            ],
        },
        "django": {
            "framework": "django",
            "target_file": "templates/base.html",
            "code": (f"{{# Before </head> in base template #}}\n{raw_script}"),
            "notes": [
                f"Expected site domain: {domain}. Include domain allowlist in CSP script-src and connect-src ({site_url}).",
            ],
        },
    }
    return guides.get(framework, guides["html"])


def _important_instructions_text(service: HotspotService, topic: str = "all") -> str:
    site_url = service.config.site_url.rstrip("/")
    sdk_url = service.config.sdk_url
    normalized = (topic or "all").strip().lower()

    sections: dict[str, str] = {
        "security": f"""## 1. Security & Key Separation (CRITICAL)
- **Public API Key (`hs_live_...`)**: Safe for client-side HTML/JS `<script data-api-key="...">`. It ONLY permits sending telemetry events and replay chunks.
- **Private Secret Key (`hs_sec_...`)**: Grants full read access to analytics, session timelines, recordings, and setup verification for that single website/domain.
  - **NEVER** put `hs_sec_...` into frontend HTML, React/Next.js client components, `NEXT_PUBLIC_*` env vars, or public git commits.
  - Provide `hs_sec_...` only via MCP client headers (`Authorization: Bearer hs_sec_...` or `X-Hotspot-Secret-Key`), `?secret_key=...`, `HOTSPOT_MCP_SECRET_KEY`, or the tool `secret_key` parameter during interactive analysis.
- **Domain Re-Registration Rule**: Calling `register_site` for a domain that already exists fails with a conflict error unless you supply the existing `confirmation_key` (`hs_sec_...`). This prevents anyone from hijacking another site's dashboard or keys.
""",
        "integration": f"""## 2. Integrating Hotspot into a Codebase
1. If the user does not have keys yet, call `register_site(name=..., domain=...)` once to generate `api_key` (`hs_live_...`) and `secret_key` (`hs_sec_...`). Save `secret_key` for MCP / dashboard login only.
2. Call `get_site_setup(framework=...)` (`html`, `nextjs_app`, `nextjs_pages`, `react_vite`, `sveltekit`, `astro`, `django`) to get the exact code snippet for their stack.
3. Add the `<script>` tag inside `<head>` with:
   - `src="{sdk_url}"`
   - `data-api-host="{site_url}"`
   - `data-api-key="<hs_live_...>"`
   - `defer`
4. If the app uses a Content Security Policy (CSP), ensure `{site_url}` is allowed in both `script-src` and `connect-src`.
5. Optional but valuable instrumentation once loaded:
   - Identify logged-in users: `window.hotspot?.identify(userId, {{ email, name, plan }})` and `window.hotspot?.reset()` on logout.
   - Track key business conversions: `window.hotspot?.capture("signup_completed", {{ plan: "pro" }})`.
   - Mask sensitive UI elements with `data-hotspot-mask` (or `.hs-mask`), block elements from DOM replay with `data-hotspot-block` (or `.hs-block`), and skip input capture with `data-hotspot-ignore`.
""",
        "sdk": f"""## 3. JavaScript SDK Reference (`window.hotspot` & `<script>` attributes)
### Script attributes (`products/hotspot/sdk/hotspot.js`)
- `data-api-key` (required): public `hs_live_...` key.
- `data-api-host`: telemetry host (`{site_url}`).
- `data-autocapture="false"`: disable automatic click/change capture.
- `data-capture-pageview="false"`: disable automatic initial & SPA route `$pageview`.
- `data-capture-errors="false"`: disable `window.onerror` / `unhandledrejection` `$exception` capture.
- `data-capture-console="false"`: disable console log/warn/error capture in replay.
- `data-capture-network="false"`: disable fetch/XHR timing/status capture in replay.
- `data-capture-web-vitals="false"`: disable CLS, FCP, INP, LCP, TTFB capture (`$web_vitals`).
- `data-replay="false"`: disable DOM recording until consent (`window.hotspot.startSessionRecording()`).
- `data-mask-inputs="all"`: mask every form input value in replay.
- `data-mask-text="true"`: mask every text node in replay.
- `data-debug="true"`: log captured events in browser console.
- `data-debug-hud="true"`: enable interactive on-page click overlay (`Alt+H`).

### Public methods on `window.hotspot`
- `window.hotspot.capture(eventName, properties)` (NOTE: method is `.capture(...)`, NOT `.track(...)`)
- `window.hotspot.identify(distinctId, properties)`
- `window.hotspot.setPersonProperties(properties)`
- `window.hotspot.register(properties)` (super-properties sent with every subsequent event)
- `window.hotspot.reset()` (clears distinct_id, session_id, super-properties on logout)
- `window.hotspot.optOut()` / `window.hotspot.optIn()` / `window.hotspot.hasOptedOut()`
- `window.hotspot.startSessionRecording()` / `window.hotspot.stopSessionRecording()` / `window.hotspot.isSessionRecording()`
- `window.hotspot.getDistinctId()` / `window.hotspot.getSessionId()`
- `window.hotspot.on(eventName, callback)`
- `window.hotspot.flush()`
- `window.hotspot.debug.show()` / `.hide()` / `.clear()` / `.getClicks()`

### Privacy DOM Attributes
- `data-hotspot-mask` or `.hs-mask`: masks element text and input values in replay.
- `data-hotspot-block` or `.hs-block`: replaces element with an empty same-size box in replay.
- `data-hotspot-ignore`: skips input events on that element.
""",
        "testing": """## 4. Testing & Verifying Sessions
1. **Live Browser Check**: Run the user's app locally or in staging, open a page, trigger key interactions (navigation, button clicks, forms), wait 2-5 seconds (or call `window.hotspot.flush()`), then call `verify_connection`.
2. **Synthetic End-to-End Test**: Call `send_test_session` to ingest a realistic multi-event session (`$pageview`, `$autocapture`, `$web_vitals`, optional conversion/error, and rrweb replay chunk with console/network logs) directly through Hotspot's real ingestion pipeline for the scoped site.
3. **Inspect Session & Replay Diagnostics**: Call `list_sessions` to find the `session_id`, then call `get_session_detail(session_id=..., include_recording_diagnostics=True)` to inspect the chronological timeline alongside extracted console errors and slow/failed network requests.
""",
        "charts": """## 5. Pulling Data & Rendering Charts
- Use `get_chart_data(chart_type=..., hours=...)` for normalized, visualization-ready arrays (`trend_timeseries`, `conversion_funnel`, `top_pages`, `traffic_channels`, `device_browser_breakdown`, `realtime_activity`, or `all`).
- `trend_timeseries.points` includes UTC `iso_time`, `timestamp`, `sessions`, `visitors`, `pageviews`, `converted`, and `conversion_rate`, ready for Recharts, Chart.js, Vega-Lite, Matplotlib, or SVG/Markdown tables.
- Use `get_funnel_analysis` to evaluate custom funnel steps (e.g. `["event:$pageview", "path:/pricing", "text:Start free trial", "event:$form_submit"]`) AND inspect `suggested_funnels` auto-discovered from real visitor journeys.
- Use `get_user_flows`, `get_breakage_and_errors`, `get_pages_and_vitals`, `get_referrers_and_ai_traffic`, `get_realtime_snapshot`, and `get_live_events` for deep diagnostic reports.
""",
    }

    if normalized in sections:
        return f"# Hotspot MCP Instructions ({normalized})\n\n{sections[normalized]}".strip()

    ordered = [sections[k] for k in ("security", "integration", "sdk", "testing", "charts")]
    header = (
        f"# Hotspot MCP Suite Instructions (v{__version__})\n"
        f"Server URL: `{site_url}` | MCP Streamable HTTP: `{site_url}/mcp` | Legacy SSE: `{site_url}/mcp/sse`\n\n"
    )
    return header + "\n".join(ordered).strip()


def resolve_mcp_auth(
    service: HotspotService,
    *,
    headers: Mapping[str, str] | None = None,
    query_params: Mapping[str, str] | None = None,
    arg_secret_key: str | None = None,
    require: bool = True,
) -> McpAuthContext | None:
    """Resolve and strictly enforce secret-key tenant scoping for MCP calls.

    - Explicit HTTP headers/query credentials are validated immediately; invalid values raise McpAuthError.
    - Public `hs_live_...` keys are rejected because MCP data access requires `hs_sec_...`.
    - When `require=True`, at least one valid credential (header, query, env/CLI, or `arg_secret_key`) must exist.
    """
    lowered = {k.lower(): v for k, v in (headers or {}).items() if v}
    header_token: str | None = None

    auth_header = lowered.get("authorization", "").strip()
    if auth_header:
        if auth_header.lower().startswith("bearer "):
            header_token = auth_header[7:].strip()
        else:
            raise McpAuthError("Authorization header must use Bearer <secret_key>.")
    elif lowered.get("x-hotspot-secret-key"):
        header_token = lowered["x-hotspot-secret-key"].strip()
    elif lowered.get("x-api-key"):
        header_token = lowered["x-api-key"].strip()
    elif query_params:
        for param_name in ("secret_key", "token"):
            val = (query_params.get(param_name) or "").strip()
            if val:
                header_token = val
                break

    def _validate_secret(raw_key: str) -> McpAuthContext:
        cleaned = raw_key.strip()
        if not cleaned:
            raise McpAuthError("Secret key cannot be empty.")
        if cleaned.startswith("hs_live_"):
            raise McpAuthError(
                "Public API key (`hs_live_...`) cannot authenticate MCP data tools. "
                "Pass your site's private Secret Key (`hs_sec_...`)."
            )
        if service.config.dashboard_password and password_matches(service.config.dashboard_password, cleaned):
            return McpAuthContext(scope="*", site=None, is_admin=True, secret_key_hint="admin")
        site = service.store.get_site_by_secret_key(cleaned)
        if site is None:
            raise McpAuthError("Invalid secret key. Verify your `hs_sec_...` key for this site.")
        return McpAuthContext(
            scope=str(site.get("domain") or site.get("id") or ""),
            site=site,
            is_admin=False,
            secret_key_hint=_mask_key(cleaned),
        )

    header_ctx: McpAuthContext | None = None
    if header_token is not None:
        header_ctx = _validate_secret(header_token)

    cleaned_arg = arg_secret_key.strip() if isinstance(arg_secret_key, str) and arg_secret_key.strip() else None
    arg_ctx: McpAuthContext | None = None
    if cleaned_arg is not None:
        arg_ctx = _validate_secret(cleaned_arg)

    if header_ctx is not None and arg_ctx is not None:
        if not header_ctx.is_admin and (arg_ctx.is_admin or header_ctx.scope.lower() != arg_ctx.scope.lower()):
            raise McpAuthError(
                f"Credential mismatch: HTTP session is scoped to '{header_ctx.scope}' and cannot read "
                f"another site's secret key ('{arg_ctx.scope}')."
            )
        return arg_ctx if header_ctx.is_admin else header_ctx

    resolved = arg_ctx or header_ctx
    if resolved is None and require:
        raise McpAuthError(
            "Authentication required: supply your site Secret Key (`hs_sec_...`) via `secret_key` argument, "
            "`Authorization: Bearer hs_sec_...` HTTP header, `X-Hotspot-Secret-Key` header, or `?secret_key=...` query param."
        )
    return resolved


def _extract_secret_arg(arguments: Mapping[str, Any]) -> str | None:
    raw = arguments.get("secret_key")
    return raw.strip() if isinstance(raw, str) and raw.strip() else None


def _target_site_for(
    service: HotspotService,
    auth: McpAuthContext,
    requested_site: str | None = None,
) -> dict[str, Any] | None:
    if auth.site is not None and not auth.is_admin:
        if requested_site:
            req = requested_site.strip().lower()
            candidates = {
                (auth.site.get("domain") or "").lower(),
                (auth.site.get("id") or "").lower(),
                (auth.site.get("api_key") or "").lower(),
            }
            if req and req not in candidates:
                raise McpAuthError(
                    f"Access denied: secret key is scoped strictly to '{auth.site.get('domain')}', not '{requested_site}'."
                )
        return auth.site

    sites = service.list_sites(authorized_scope="*")
    if not sites:
        return None
    if requested_site:
        req = requested_site.strip().lower()
        for site in sites:
            if req in {
                (site.get("domain") or "").lower(),
                (site.get("id") or "").lower(),
                (site.get("api_key") or "").lower(),
            }:
                return site
        return None
    return sites[0]


def _query_params_from_args(auth: McpAuthContext, arguments: Mapping[str, Any]) -> dict[str, str]:
    params: dict[str, str] = {}
    if auth.site and not auth.is_admin:
        params["site"] = str(auth.site.get("domain") or "")
    elif isinstance(arguments.get("site"), str) and arguments["site"].strip():
        params["site"] = arguments["site"].strip()

    for key in ("hours", "limit", "tz_offset"):
        val = arguments.get(key)
        if val is not None and not isinstance(val, bool):
            params[key] = str(int(val))

    for key in ("status", "search", "device_type", "browser", "os", "funnel"):
        val = arguments.get(key)
        if isinstance(val, str) and val.strip():
            params[key] = val.strip()

    steps = arguments.get("steps")
    if isinstance(steps, list) and steps:
        cleaned = [str(item).strip() for item in steps if isinstance(item, str) and item.strip()]
        if cleaned:
            params["funnel"] = "|".join(cleaned)
    return params


def _extract_replay_diagnostics(recording_json: str, *, include_raw_events: bool = False) -> dict[str, Any]:
    try:
        parsed = json.loads(recording_json)
    except ValueError:
        return {"present": False, "error": "Failed to parse compressed recording stream"}

    events = parsed.get("events") if isinstance(parsed, dict) else None
    if not isinstance(events, list):
        return {"present": False, "total_events": 0, "console_logs": [], "network_requests": []}

    type_counts: dict[str, int] = {}
    type_names = {
        0: "dom_content_loaded",
        1: "load",
        2: "full_snapshot",
        3: "incremental_snapshot",
        4: "meta",
        5: "custom",
        6: "plugin",
    }
    console_logs: list[dict[str, Any]] = []
    network_requests: list[dict[str, Any]] = []
    errors_or_warnings = 0
    failed_requests = 0

    for item in events:
        if not isinstance(item, dict):
            continue
        ev_type = item.get("type")
        type_label = type_names.get(ev_type, f"type_{ev_type}")
        type_counts[type_label] = type_counts.get(type_label, 0) + 1
        if ev_type != 5:
            continue
        data = item.get("data")
        if not isinstance(data, dict):
            continue
        tag = data.get("tag")
        payload = data.get("payload")
        if not isinstance(payload, dict):
            continue
        ts_ms = item.get("timestamp")
        iso = _iso_utc(float(ts_ms) / 1000.0) if isinstance(ts_ms, (int, float)) else None
        if tag == "console":
            level = str(payload.get("level") or "log").lower()
            if level in {"error", "warn", "warning"}:
                errors_or_warnings += 1
            if len(console_logs) < 100:
                console_logs.append(
                    {
                        "timestamp_ms": ts_ms,
                        "iso_time": iso,
                        "level": level,
                        "message": payload.get("message") or payload.get("payload") or payload,
                    }
                )
        elif tag == "network":
            status = payload.get("status")
            if isinstance(status, int) and (status == 0 or status >= 400):
                failed_requests += 1
            if len(network_requests) < 100:
                network_requests.append(
                    {
                        "timestamp_ms": ts_ms,
                        "iso_time": iso,
                        "method": payload.get("method"),
                        "url": payload.get("url"),
                        "status": status,
                        "duration_ms": payload.get("duration") or payload.get("duration_ms"),
                        "error": payload.get("error"),
                    }
                )

    result: dict[str, Any] = {
        "present": len(events) > 0,
        "truncated": bool(parsed.get("truncated")),
        "started_at": parsed.get("started_at"),
        "total_recording_events": len(events),
        "event_type_counts": type_counts,
        "console_errors_or_warnings_count": errors_or_warnings,
        "failed_network_requests_count": failed_requests,
        "console_logs": console_logs,
        "network_requests": network_requests,
    }
    if include_raw_events:
        result["raw_events_sample"] = events[:150]
        result["raw_events_capped"] = len(events) > 150
    return result


def _format_trend_chart(trend: Mapping[str, Any]) -> dict[str, Any]:
    start = float(trend.get("start") or 0)
    bucket = int(trend.get("bucket_seconds") or 3600)
    sessions_arr = list(trend.get("sessions") or [])
    visitors_arr = list(trend.get("visitors") or [])
    pageviews_arr = list(trend.get("pageviews") or [])
    converted_arr = list(trend.get("converted") or [])

    points = []
    for i, sess in enumerate(sessions_arr):
        ts = start + i * bucket
        vis = visitors_arr[i] if i < len(visitors_arr) else 0
        pvs = pageviews_arr[i] if i < len(pageviews_arr) else 0
        conv = converted_arr[i] if i < len(converted_arr) else 0
        conv_rate = round((conv / sess) * 100, 1) if sess else 0.0
        dt = datetime.fromtimestamp(ts, tz=UTC)
        label = dt.strftime("%Y-%m-%d") if bucket >= 86400 else dt.strftime("%m-%d %H:%M UTC")
        points.append(
            {
                "index": i,
                "timestamp": ts,
                "iso_time": dt.isoformat(),
                "label": label,
                "sessions": sess,
                "visitors": vis,
                "pageviews": pvs,
                "converted": conv,
                "conversion_rate": conv_rate,
            }
        )

    return {
        "chart_kind": "time_series",
        "bucket_seconds": bucket,
        "granularity": "5m" if bucket == 300 else ("1h" if bucket == 3600 else "1d"),
        "x_field": "iso_time",
        "x_label_field": "label",
        "series_fields": ["sessions", "visitors", "pageviews", "converted"],
        "totals": {
            "sessions": sum(sessions_arr),
            "visitors_sum_across_buckets": sum(visitors_arr),
            "pageviews": sum(pageviews_arr),
            "converted": sum(converted_arr),
        },
        "points": points,
    }


def _dict_to_ranked_bars(counts: Mapping[str, int], total_hint: int | None = None) -> list[dict[str, Any]]:
    total = total_hint if total_hint is not None else sum(counts.values())
    rows = []
    for name, count in sorted(counts.items(), key=lambda kv: kv[1], reverse=True):
        rows.append(
            {
                "name": name,
                "count": count,
                "share_pct": round((count / total) * 100, 1) if total else 0.0,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def _tool_get_important_instructions(
    service: HotspotService, arguments: Mapping[str, Any], auth: McpAuthContext | None
) -> dict[str, Any]:
    topic = str(arguments.get("topic") or "all").strip().lower()
    return {
        "topic": topic,
        "server_url": service.config.site_url,
        "sdk_url": service.config.sdk_url,
        "authenticated_scope": auth.scope if auth else None,
        "instructions": _important_instructions_text(service, topic=topic),
    }


def _tool_register_site(
    service: HotspotService, arguments: Mapping[str, Any], auth: McpAuthContext | None
) -> dict[str, Any]:
    name = str(arguments.get("name") or "").strip()
    domain = str(arguments.get("domain") or "").strip()
    if not domain:
        raise McpProtocolError(_ERROR_INVALID_PARAMS, "`domain` is required (e.g., 'app.example.com').")
    if not name:
        name = domain

    confirmation_key = arguments.get("confirmation_key")
    conf_str = str(confirmation_key).strip() if confirmation_key else None
    if not conf_str and auth is not None:
        if auth.is_admin:
            conf_str = service.config.dashboard_password
        elif auth.site and (auth.site.get("domain") or "").lower() == domain.lower():
            conf_str = auth.site.get("secret_key")

    ok, res = service.create_site(name, domain, confirmation_key=conf_str)
    if not ok:
        raise McpProtocolError(_ERROR_INVALID_PARAMS, str(res))

    assert isinstance(res, dict)
    framework = str(arguments.get("framework") or "html").strip().lower()
    if framework not in VALID_FRAMEWORKS:
        framework = "html"

    secret_key = str(res.get("secret_key") or "")
    api_key = str(res.get("api_key") or "")
    return {
        "created_or_rotated": True,
        "site": res,
        "security_warning": (
            "IMPORTANT: Place `api_key` (hs_live_...) inside your HTML/JS <script> snippet ONLY. "
            "Keep `secret_key` (hs_sec_...) strictly private for MCP Authentication and dashboard login."
        ),
        "integration_guide": _framework_guide(service, framework, api_key, str(res.get("domain") or domain)),
        "mcp_client_config": _mcp_config_snippet(service, secret_key),
    }


def _tool_get_site_setup(
    service: HotspotService, arguments: Mapping[str, Any], auth: McpAuthContext | None
) -> dict[str, Any]:
    assert auth is not None
    site = _target_site_for(service, auth, arguments.get("site"))
    if site is None:
        raise McpProtocolError(_ERROR_INVALID_PARAMS, "No matching tracked site found for this secret key.")

    framework = str(arguments.get("framework") or "html").strip().lower()
    if framework not in VALID_FRAMEWORKS:
        raise McpProtocolError(
            _ERROR_INVALID_PARAMS,
            f"Unsupported framework '{framework}'. Choose one of: {', '.join(VALID_FRAMEWORKS)}.",
        )

    api_key = str(site.get("api_key") or "")
    domain = str(site.get("domain") or "")
    setup = service.setup()
    return {
        "site": {
            "id": site.get("id"),
            "name": site.get("name"),
            "domain": domain,
            "api_key": api_key,
            "created_at": site.get("created_at"),
            "created_at_iso": _iso_utc(site.get("created_at")),
        },
        "server": {
            "site_url": service.config.site_url,
            "sdk_url": service.config.sdk_url,
            "capture_endpoint": f"{service.config.site_url.rstrip('/')}/hotspot/batch/",
            "replay_endpoint": f"{service.config.site_url.rstrip('/')}/hotspot/replay/",
            "mcp_streamable_http_url": f"{service.config.site_url.rstrip('/')}/mcp",
            "mcp_sse_url": f"{service.config.site_url.rstrip('/')}/mcp/sse",
            "replay_enabled": setup.get("replay_enabled", service.config.replay_enabled),
            "storage_backend": setup.get("storage"),
        },
        "integration_guide": _framework_guide(service, framework, api_key, domain),
        "privacy_and_sdk_tips": {
            "identify_example": "window.hotspot?.identify('user_123', { email: 'user@example.com', plan: 'pro' });",
            "custom_event_example": "window.hotspot?.capture('checkout_started', { cart_value: 99, currency: 'USD' });",
            "mask_element_attribute": 'data-hotspot-mask (or class="hs-mask")',
            "block_element_attribute": 'data-hotspot-block (or class="hs-block")',
            "ignore_input_attribute": "data-hotspot-ignore",
            "csp_directives_required": {
                "script-src": service.config.site_url,
                "connect-src": service.config.site_url,
            },
        },
        "mcp_client_config": _mcp_config_snippet(service, site.get("secret_key")),
    }


def _tool_verify_connection(
    service: HotspotService, arguments: Mapping[str, Any], auth: McpAuthContext | None
) -> dict[str, Any]:
    assert auth is not None
    site = _target_site_for(service, auth, arguments.get("site"))
    if site is None:
        raise McpProtocolError(_ERROR_INVALID_PARAMS, "No matching tracked site found for this secret key.")

    domain = str(site.get("domain") or "")
    overview = service.overview(
        {"site": domain, "hours": str(int(arguments.get("hours") or 168)), "limit": "5"},
        authorized_scope=auth.scope,
    )
    feed = service.events_feed({"limit": "15"}, authorized_scope=auth.scope)
    recent_events = feed.get("events") or []
    sessions = overview.get("sessions") or []
    metrics = overview.get("metrics") or {}
    has_data = bool(sessions or recent_events)
    has_recording = any(bool(s.get("has_recording")) for s in sessions)

    troubleshooting: list[str] = []
    if not has_data:
        troubleshooting = [
            f'Confirm `<script src="{service.config.sdk_url}" data-api-host="{service.config.site_url}" data-api-key="{site.get("api_key")}" defer></script>` is loaded in the browser <head>.',
            "Open the browser DevTools Network tab and verify POST requests to `/hotspot/batch/` return HTTP 200 `{status: 1}`.",
            "Run `window.hotspot?.getSessionId()` and `window.hotspot?.flush()` in the DevTools Console to confirm the script initialized.",
            f"If your site enforces Content-Security-Policy, verify `{service.config.site_url}` is allowed in `script-src` and `connect-src`.",
            "Or call `send_test_session` from MCP to verify ingestion & replay storage for this secret key right away.",
        ]

    return {
        "connected": has_data,
        "status": "receiving_telemetry" if has_data else "awaiting_first_event",
        "site": {
            "name": site.get("name"),
            "domain": domain,
            "api_key": site.get("api_key"),
        },
        "telemetry_summary": {
            "sessions_in_window": metrics.get("sessions", 0),
            "visitors_in_window": metrics.get("visitors", 0),
            "pageviews_in_window": metrics.get("pageviews", 0),
            "active_now": (overview.get("realtime") or {}).get("active_now", 0),
            "has_session_recordings": has_recording,
            "recent_events_count": len(recent_events),
        },
        "latest_session": sessions[0] if sessions else None,
        "recent_events_sample": recent_events[:5],
        "troubleshooting": troubleshooting,
    }


def _tool_send_test_session(
    service: HotspotService, arguments: Mapping[str, Any], auth: McpAuthContext | None
) -> dict[str, Any]:
    assert auth is not None
    site = _target_site_for(service, auth, arguments.get("site"))
    if site is None:
        raise McpProtocolError(_ERROR_INVALID_PARAMS, "No matching tracked site found for this secret key.")

    api_key = str(site.get("api_key") or "")
    domain = str(site.get("domain") or "example.com")
    now = time.time()
    session_id = f"mcp-test-{uuid.uuid4().hex[:10]}"
    distinct_id = str(arguments.get("distinct_id") or f"mcp-tester-{uuid.uuid4().hex[:6]}")
    entry_path = str(arguments.get("entry_path") or "/").strip() or "/"
    if not entry_path.startswith("/"):
        entry_path = f"/{entry_path}"
    secondary_path = str(arguments.get("secondary_path") or "/pricing").strip() or "/pricing"
    if not secondary_path.startswith("/"):
        secondary_path = f"/{secondary_path}"
    include_conversion = bool(arguments.get("include_conversion", True))
    include_error = bool(arguments.get("include_error", False))
    include_replay = bool(arguments.get("include_replay", True))
    conversion_event = str(arguments.get("conversion_event") or "$form_submit")

    base_url = f"https://{domain}"
    common_props = {
        "$session_id": session_id,
        "$host": domain,
        "$browser": "Chrome",
        "$browser_version": "131",
        "$os": "macOS",
        "$device_type": "desktop",
        "$viewport_width": 1440,
        "$viewport_height": 900,
        "$country": "US",
        "$referrer": "https://chatgpt.com/",
        "$is_ai_referral": True,
        "$ai_source": "ChatGPT",
        "$mcp_synthetic_test": True,
    }

    batch: list[dict[str, Any]] = [
        {
            "event": "$pageview",
            "distinct_id": distinct_id,
            "timestamp": now - 16,
            "properties": {
                **common_props,
                "$pathname": entry_path,
                "$current_url": f"{base_url}{entry_path}",
                "$title": f"{site.get('name') or domain} - Home",
            },
        },
        {
            "event": "$autocapture",
            "distinct_id": distinct_id,
            "timestamp": now - 12,
            "properties": {
                **common_props,
                "$pathname": entry_path,
                "$current_url": f"{base_url}{entry_path}",
                "$el_selector": "button#primary-cta",
                "$el_tag_name": "button",
                "$el_text": "Get started",
            },
        },
        {
            "event": "$web_vitals",
            "distinct_id": distinct_id,
            "timestamp": now - 10,
            "properties": {
                **common_props,
                "$pathname": entry_path,
                "$lcp_ms": 1180,
                "$fcp_ms": 540,
                "$inp_ms": 62,
                "$ttfb_ms": 180,
                "$cls": 0.02,
            },
        },
        {
            "event": "$pageview",
            "distinct_id": distinct_id,
            "timestamp": now - 8,
            "properties": {
                **common_props,
                "$pathname": secondary_path,
                "$current_url": f"{base_url}{secondary_path}",
                "$title": f"{site.get('name') or domain} - {secondary_path.strip('/') or 'Page'}",
            },
        },
    ]

    if include_error:
        batch.append(
            {
                "event": "$rageclick",
                "distinct_id": distinct_id,
                "timestamp": now - 5,
                "properties": {
                    **common_props,
                    "$pathname": secondary_path,
                    "$el_selector": "button#checkout-submit",
                    "$el_tag_name": "button",
                    "$el_text": "Continue",
                },
            }
        )
        batch.append(
            {
                "event": "$exception",
                "distinct_id": distinct_id,
                "timestamp": now - 4,
                "properties": {
                    **common_props,
                    "$pathname": secondary_path,
                    "$exception_type": "TypeError",
                    "$exception_message": "Synthetic test error: Cannot read properties of undefined (reading 'submit')",
                },
            }
        )

    if include_conversion:
        batch.append(
            {
                "event": conversion_event,
                "distinct_id": distinct_id,
                "timestamp": now - 2,
                "properties": {
                    **common_props,
                    "$pathname": secondary_path,
                    "$current_url": f"{base_url}{secondary_path}",
                },
            }
        )

    batch.append(
        {
            "event": "$pageleave",
            "distinct_id": distinct_id,
            "timestamp": now - 1,
            "properties": {
                **common_props,
                "$pathname": secondary_path,
                "$duration_seconds": 15,
                "$scroll_depth_percent": 80,
            },
        }
    )

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
    }
    cap_status, cap_res = service.capture(
        json.dumps({"api_key": api_key, "batch": batch}).encode("utf-8"),
        headers,
        now=now,
    )
    if cap_status != 200:
        raise McpProtocolError(_ERROR_INTERNAL, f"Synthetic capture failed: {cap_res}")

    rec_res: dict[str, Any] | None = None
    if include_replay and service.config.replay_enabled:
        now_ms = int(now * 1000)
        rrweb_events: list[dict[str, Any]] = [
            {
                "type": 4,
                "data": {"href": f"{base_url}{entry_path}", "width": 1440, "height": 900},
                "timestamp": now_ms - 16000,
            },
            {
                "type": 2,
                "data": {
                    "node": {
                        "type": 0,
                        "id": 1,
                        "childNodes": [
                            {"type": 1, "id": 2, "name": "html", "publicId": "", "systemId": ""},
                            {
                                "type": 2,
                                "id": 3,
                                "tagName": "html",
                                "attributes": {},
                                "childNodes": [
                                    {"type": 2, "id": 4, "tagName": "head", "attributes": {}, "childNodes": []},
                                    {
                                        "type": 2,
                                        "id": 5,
                                        "tagName": "body",
                                        "attributes": {
                                            "style": "font-family:system-ui,sans-serif;padding:48px;background:#fff;color:#0f172a;"
                                        },
                                        "childNodes": [
                                            {
                                                "type": 2,
                                                "id": 6,
                                                "tagName": "h1",
                                                "attributes": {},
                                                "childNodes": [
                                                    {
                                                        "type": 3,
                                                        "id": 7,
                                                        "textContent": f"{site.get('name') or domain} — MCP Test Session",
                                                    }
                                                ],
                                            },
                                            {
                                                "type": 2,
                                                "id": 8,
                                                "tagName": "button",
                                                "attributes": {"id": "primary-cta"},
                                                "childNodes": [{"type": 3, "id": 9, "textContent": "Get started"}],
                                            },
                                        ],
                                    },
                                ],
                            },
                        ],
                    },
                    "initialOffset": {"left": 0, "top": 0},
                },
                "timestamp": now_ms - 15900,
            },
            {
                "type": 5,
                "data": {
                    "tag": "console",
                    "payload": {
                        "level": "error" if include_error else "info",
                        "message": [
                            "Synthetic TypeError during checkout test"
                            if include_error
                            else "[Hotspot MCP] Synthetic test session active"
                        ],
                    },
                },
                "timestamp": now_ms - 5000,
            },
            {
                "type": 5,
                "data": {
                    "tag": "network",
                    "payload": {
                        "method": "POST" if include_error else "GET",
                        "url": f"{base_url}/api/checkout" if include_error else f"{base_url}/api/health",
                        "status": 500 if include_error else 200,
                        "duration": 420 if include_error else 48,
                    },
                },
                "timestamp": now_ms - 4500,
            },
        ]
        rec_status, rec_payload = service.capture_recording(
            json.dumps(
                {
                    "api_key": api_key,
                    "session_id": session_id,
                    "distinct_id": distinct_id,
                    "events": rrweb_events,
                }
            ).encode("utf-8"),
            {},
            now=now,
        )
        rec_res = {"http_status": rec_status, **rec_payload}

    detail = service.session_detail(session_id, authorized_scope=auth.scope, now=now)
    return {
        "created": True,
        "site": {"name": site.get("name"), "domain": domain},
        "session_id": session_id,
        "distinct_id": distinct_id,
        "events_ingested": cap_res.get("received", len(batch)),
        "recording": rec_res,
        "session": detail.get("session") if detail else None,
        "next_steps": [
            f"Call `get_session_detail(session_id='{session_id}')` to inspect its full timeline and console/network diagnostics.",
            "Call `get_chart_data(chart_type='all')` or `verify_connection` to confirm charts and metrics reflect this session.",
        ],
    }


def _tool_get_overview(
    service: HotspotService, arguments: Mapping[str, Any], auth: McpAuthContext | None
) -> dict[str, Any]:
    assert auth is not None
    params = _query_params_from_args(auth, arguments)
    if "limit" not in params:
        params["limit"] = "20"
    data = service.overview(params, authorized_scope=auth.scope)
    return {
        "generated_at": data.get("generated_at"),
        "generated_at_iso": _iso_utc(data.get("generated_at")),
        "scope": data.get("scope"),
        "sites": data.get("sites"),
        "metrics": data.get("metrics"),
        "comparison": data.get("comparison"),
        "web_vitals": data.get("web_vitals"),
        "breakdowns": data.get("breakdowns"),
        "realtime_summary": {
            "active_now": (data.get("realtime") or {}).get("active_now", 0),
            "active_in_window": (data.get("realtime") or {}).get("active_in_window", 0),
        },
        "recent_sessions": data.get("sessions") or [],
    }


def _tool_get_chart_data(
    service: HotspotService, arguments: Mapping[str, Any], auth: McpAuthContext | None
) -> dict[str, Any]:
    assert auth is not None
    chart_type = str(arguments.get("chart_type") or "all").strip().lower()
    if chart_type not in VALID_CHART_TYPES:
        raise McpProtocolError(
            _ERROR_INVALID_PARAMS,
            f"Invalid `chart_type` '{chart_type}'. Choose one of: {', '.join(VALID_CHART_TYPES)}.",
        )

    params = _query_params_from_args(auth, arguments)
    params.setdefault("limit", "20")
    data = service.overview(params, authorized_scope=auth.scope)

    charts: dict[str, Any] = {}
    want_all = chart_type == "all"

    if want_all or chart_type == "trend_timeseries":
        charts["trend_timeseries"] = _format_trend_chart(data.get("trend") or {})

    if want_all or chart_type == "conversion_funnel":
        funnel = data.get("funnel") or {}
        charts["conversion_funnel"] = {
            "chart_kind": "funnel_bar",
            "x_field": "label",
            "y_field": "count",
            "conversion_rate": funnel.get("conversion_rate", 0.0),
            "sessions_analyzed": funnel.get("sessions_analyzed", 0),
            "is_custom": funnel.get("is_custom", False),
            "steps": funnel.get("steps") or [],
            "suggested_funnels": funnel.get("suggested_funnels") or [],
        }

    if want_all or chart_type == "top_pages":
        pages = data.get("pages") or []
        charts["top_pages"] = {
            "chart_kind": "horizontal_bar",
            "x_field": "pathname",
            "y_fields": ["views", "sessions", "conversion_rate", "exit_rate"],
            "rows": pages[:20],
        }

    if want_all or chart_type == "traffic_channels":
        refs = data.get("referrers") or {}
        charts["traffic_channels"] = {
            "chart_kind": "categorical_bar",
            "x_field": "channel",
            "y_fields": ["sessions", "converted", "conversion_rate"],
            "channels": refs.get("channels") or [],
            "top_sources": (refs.get("sources") or [])[:15],
            "ai_sessions": refs.get("ai_sessions", 0),
            "ai_sources": refs.get("ai_sources") or [],
        }

    if want_all or chart_type == "device_browser_breakdown":
        breakdowns = data.get("breakdowns") or {}
        total_sessions = int((data.get("metrics") or {}).get("sessions") or 0) or None
        charts["device_browser_breakdown"] = {
            "chart_kind": "donut_or_bar",
            "device_type": _dict_to_ranked_bars(breakdowns.get("device_type") or {}, total_sessions),
            "browser": _dict_to_ranked_bars(breakdowns.get("browser") or {}, total_sessions),
            "os": _dict_to_ranked_bars(breakdowns.get("os") or {}, total_sessions),
            "country": _dict_to_ranked_bars(breakdowns.get("country") or {}, total_sessions),
        }

    if want_all or chart_type == "realtime_activity":
        realtime = data.get("realtime") or {}
        activity = data.get("activity") or {}
        spm = list(realtime.get("sessions_per_minute") or [])
        epm = list(activity.get("events_per_minute") or [])
        charts["realtime_activity"] = {
            "chart_kind": "minute_sparkline",
            "active_now": realtime.get("active_now", 0),
            "active_in_30m_window": realtime.get("active_in_window", 0),
            "sessions_per_minute_last_30m": [
                {"minute_offset": -(len(spm) - 1 - idx), "sessions": val} for idx, val in enumerate(spm)
            ],
            "events_per_minute_last_60m": [
                {"minute_offset": -(len(epm) - 1 - idx), "events": val} for idx, val in enumerate(epm)
            ],
            "countries": realtime.get("countries") or [],
            "live_pages": realtime.get("pages") or [],
        }

    return {
        "scope": data.get("scope"),
        "metrics": data.get("metrics"),
        "chart_type": chart_type,
        "charts": charts,
    }


def _tool_get_funnel_analysis(
    service: HotspotService, arguments: Mapping[str, Any], auth: McpAuthContext | None
) -> dict[str, Any]:
    assert auth is not None
    params = _query_params_from_args(auth, arguments)
    params.setdefault("limit", "10")
    data = service.overview(params, authorized_scope=auth.scope)
    funnel = data.get("funnel") or {}
    return {
        "scope": data.get("scope"),
        "funnel": funnel,
        "supported_step_formats": [
            "event:<name> (e.g. event:$pageview, event:$autocapture, event:$form_submit, event:signup_completed)",
            "path:<pathname_or_prefix*> (e.g. path:/, path:/pricing, path:/blog/*)",
            "click:<css_selector_or_prefix*> (e.g. click:button#cta)",
            "text:<button_text> (e.g. text:Start free trial)",
        ],
    }


def _tool_get_user_flows(
    service: HotspotService, arguments: Mapping[str, Any], auth: McpAuthContext | None
) -> dict[str, Any]:
    assert auth is not None
    params = _query_params_from_args(auth, arguments)
    params.setdefault("limit", "10")
    data = service.overview(params, authorized_scope=auth.scope)
    return {
        "scope": data.get("scope"),
        "paths": data.get("paths") or {},
    }


def _tool_get_breakage_and_errors(
    service: HotspotService, arguments: Mapping[str, Any], auth: McpAuthContext | None
) -> dict[str, Any]:
    assert auth is not None
    params = _query_params_from_args(auth, arguments)
    params["status"] = "broken"
    params.setdefault("limit", "25")
    data = service.overview(params, authorized_scope=auth.scope)
    return {
        "scope": data.get("scope"),
        "friction": data.get("friction") or {},
        "errors": data.get("errors") or [],
        "broken_sessions": data.get("sessions") or [],
    }


def _tool_get_pages_and_vitals(
    service: HotspotService, arguments: Mapping[str, Any], auth: McpAuthContext | None
) -> dict[str, Any]:
    assert auth is not None
    params = _query_params_from_args(auth, arguments)
    params.setdefault("limit", "10")
    data = service.overview(params, authorized_scope=auth.scope)
    return {
        "scope": data.get("scope"),
        "site_web_vitals": data.get("web_vitals") or {},
        "pages": data.get("pages") or [],
    }


def _tool_get_referrers_and_ai_traffic(
    service: HotspotService, arguments: Mapping[str, Any], auth: McpAuthContext | None
) -> dict[str, Any]:
    assert auth is not None
    params = _query_params_from_args(auth, arguments)
    params.setdefault("limit", "10")
    data = service.overview(params, authorized_scope=auth.scope)
    return {
        "scope": data.get("scope"),
        "referrers": data.get("referrers") or {},
    }


def _tool_get_realtime_snapshot(
    service: HotspotService, arguments: Mapping[str, Any], auth: McpAuthContext | None
) -> dict[str, Any]:
    assert auth is not None
    params = _query_params_from_args(auth, arguments)
    params["hours"] = "1"
    params["status"] = "active"
    params.setdefault("limit", "25")
    data = service.overview(params, authorized_scope=auth.scope)
    return {
        "generated_at": data.get("generated_at"),
        "generated_at_iso": _iso_utc(data.get("generated_at")),
        "scope": data.get("scope"),
        "realtime": data.get("realtime") or {},
        "activity": data.get("activity") or {},
        "active_sessions": data.get("sessions") or [],
    }


def _tool_list_sessions(
    service: HotspotService, arguments: Mapping[str, Any], auth: McpAuthContext | None
) -> dict[str, Any]:
    assert auth is not None
    params = _query_params_from_args(auth, arguments)
    params.setdefault("limit", "50")
    data = service.overview(params, authorized_scope=auth.scope)
    return {
        "scope": data.get("scope"),
        "total_in_range": (data.get("scope") or {}).get("sessions_analyzed", 0),
        "returned": len(data.get("sessions") or []),
        "sessions": data.get("sessions") or [],
    }


def _tool_get_session_detail(
    service: HotspotService, arguments: Mapping[str, Any], auth: McpAuthContext | None
) -> dict[str, Any]:
    assert auth is not None
    session_id = str(arguments.get("session_id") or "").strip()
    if not session_id:
        raise McpProtocolError(_ERROR_INVALID_PARAMS, "`session_id` is required.")

    detail = service.session_detail(session_id, authorized_scope=auth.scope)
    if detail is None:
        raise McpProtocolError(
            _ERROR_INVALID_PARAMS,
            f"Session '{session_id}' was not found or is not accessible with this secret key.",
        )

    include_rec = bool(arguments.get("include_recording_diagnostics", True))
    include_raw = bool(arguments.get("include_raw_recording_events", False))
    recording_diag: dict[str, Any] | None = None
    if include_rec:
        rec_raw = service.recording(session_id, {"from": "0"}, authorized_scope=auth.scope)
        if rec_raw is not None:
            recording_diag = _extract_replay_diagnostics(rec_raw, include_raw_events=include_raw)

    timeline = []
    for ev in detail.get("events") or []:
        timeline.append(
            {
                "event": ev.get("event"),
                "timestamp": ev.get("timestamp"),
                "iso_time": _iso_utc(ev.get("timestamp")),
                "properties": ev.get("properties") or {},
            }
        )

    return {
        "session": detail.get("session"),
        "event_count": len(timeline),
        "events": timeline,
        "recording_diagnostics": recording_diag,
    }


def _tool_get_live_events(
    service: HotspotService, arguments: Mapping[str, Any], auth: McpAuthContext | None
) -> dict[str, Any]:
    assert auth is not None
    params: dict[str, str] = {}
    if arguments.get("since") is not None:
        params["since"] = str(arguments["since"])
    if arguments.get("limit") is not None:
        params["limit"] = str(int(arguments["limit"]))

    feed = service.events_feed(params, authorized_scope=auth.scope)
    events = list(feed.get("events") or [])
    event_name = str(arguments.get("event_name") or "").strip()
    if event_name:
        events = [e for e in events if e.get("event") == event_name]

    enriched = []
    for ev in events:
        item = dict(ev)
        item["iso_time"] = _iso_utc(ev.get("timestamp"))
        enriched.append(item)

    return {
        "now": feed.get("now"),
        "now_iso": _iso_utc(feed.get("now")),
        "count": len(enriched),
        "events": enriched,
    }


def _tool_delete_site(
    service: HotspotService, arguments: Mapping[str, Any], auth: McpAuthContext | None
) -> dict[str, Any]:
    assert auth is not None
    if not arguments.get("confirm"):
        raise McpProtocolError(_ERROR_INVALID_PARAMS, "Pass `confirm: true` to confirm deleting this tracked site.")

    site = _target_site_for(service, auth, arguments.get("site"))
    if site is None:
        raise McpProtocolError(_ERROR_INVALID_PARAMS, "No matching tracked site found for this secret key.")

    site_id = str(site.get("id") or "")
    deleted = service.delete_site(site_id)
    return {
        "deleted": deleted,
        "site_id": site_id,
        "domain": site.get("domain"),
    }


# ---------------------------------------------------------------------------
# Tool, Resource & Prompt registries
# ---------------------------------------------------------------------------

_SECRET_KEY_PROP = {
    "type": "string",
    "description": (
        "Site Secret Key (`hs_sec_...`) if not already configured via `Authorization: Bearer` header or "
        "`HOTSPOT_MCP_SECRET_KEY` environment variable."
    ),
}

_FILTER_PROPS = {
    "secret_key": _SECRET_KEY_PROP,
    "site": {"type": "string", "description": "Optional domain (only needed with an admin key)."},
    "hours": {
        "type": "integer",
        "description": "Lookback window in hours (default 24; use 0 for all stored time).",
        "minimum": 0,
        "maximum": 2160,
    },
    "device_type": {"type": "string", "description": "Filter by device_type (e.g., desktop, mobile, tablet)."},
    "browser": {"type": "string", "description": "Filter by browser (e.g., Chrome, Safari, Firefox)."},
    "os": {"type": "string", "description": "Filter by OS (e.g., macOS, Windows, iOS, Android)."},
    "search": {"type": "string", "description": "Search query matching identity, page path, referrer, or session id."},
    "tz_offset": {"type": "integer", "description": "Viewer UTC offset in minutes for bucket alignment (default 0)."},
}

_RO_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


@dataclass(frozen=True)
class _ToolSpec:
    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    annotations: dict[str, Any]
    requires_auth: bool
    handler: Callable[[HotspotService, Mapping[str, Any], McpAuthContext | None], dict[str, Any]]


_TOOLS: tuple[_ToolSpec, ...] = (
    _ToolSpec(
        name="get_important_instructions",
        title="Read Hotspot Setup & Security Instructions",
        description=(
            "Read essential Hotspot integration instructions, key security rules (`hs_live_...` vs `hs_sec_...`), "
            "framework embed guides, `window.hotspot` JavaScript SDK reference, testing steps, and chart schemas. "
            "Call this first before modifying code or analyzing analytics."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "enum": ["all", "security", "integration", "sdk", "testing", "charts"],
                    "description": "Instruction section to read (defaults to 'all').",
                }
            },
        },
        annotations=_RO_ANNOTATIONS,
        requires_auth=False,
        handler=_tool_get_important_instructions,
    ),
    _ToolSpec(
        name="register_site",
        title="Register Website & Issue Keys",
        description=(
            "Register a website domain in Hotspot and receive its public tracking API key (`hs_live_...`), "
            "private Secret Key (`hs_sec_...`), ready-to-paste `<script>` / framework code, and MCP config. "
            "If the domain already exists, requires `confirmation_key` matching its current Secret Key to rotate keys."
        ),
        input_schema={
            "type": "object",
            "required": ["domain"],
            "properties": {
                "name": {"type": "string", "description": "Display name (e.g., 'Acme App'). Defaults to domain."},
                "domain": {"type": "string", "description": "Hostname to track (e.g., 'app.example.com')."},
                "confirmation_key": {
                    "type": "string",
                    "description": "Required when re-registering an existing domain to prove ownership (existing `hs_sec_...`).",
                },
                "framework": {
                    "type": "string",
                    "enum": list(VALID_FRAMEWORKS),
                    "description": "Preferred integration guide to return alongside the snippet.",
                },
            },
        },
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
        requires_auth=False,
        handler=_tool_register_site,
    ),
    _ToolSpec(
        name="get_site_setup",
        title="Get Site Code Snippet & Setup",
        description=(
            "Fetch framework-specific Hotspot installation code, SDK configuration tips, CSP domains, "
            "and MCP connection details for the site authenticated by your Secret Key."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "secret_key": _SECRET_KEY_PROP,
                "site": {"type": "string", "description": "Target domain (if using an admin key)."},
                "framework": {
                    "type": "string",
                    "enum": list(VALID_FRAMEWORKS),
                    "description": "Framework template to generate (default 'html').",
                },
            },
        },
        annotations=_RO_ANNOTATIONS,
        requires_auth=True,
        handler=_tool_get_site_setup,
    ),
    _ToolSpec(
        name="verify_connection",
        title="Verify Live Telemetry Connection",
        description=(
            "Check whether your website is actively sending events and session replays to Hotspot for your Secret Key, "
            "returning sample events and concrete troubleshooting steps if awaiting first data."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "secret_key": _SECRET_KEY_PROP,
                "site": {"type": "string"},
                "hours": {"type": "integer", "description": "Lookback window in hours (default 168 / 7 days)."},
            },
        },
        annotations=_RO_ANNOTATIONS,
        requires_auth=True,
        handler=_tool_verify_connection,
    ),
    _ToolSpec(
        name="send_test_session",
        title="Send Synthetic Test Session",
        description=(
            "Inject a realistic synthetic session (pageviews, autocapture click, web vitals, optional form conversion "
            "or error/rageclick, and rrweb replay chunk with console/network logs) directly through Hotspot's capture pipeline "
            "for your Secret Key's site so you can test and inspect sessions end-to-end immediately."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "secret_key": _SECRET_KEY_PROP,
                "site": {"type": "string"},
                "entry_path": {"type": "string", "description": "Landing path (default '/')."},
                "secondary_path": {"type": "string", "description": "Second visited path (default '/pricing')."},
                "distinct_id": {"type": "string", "description": "Optional synthetic visitor distinct_id."},
                "conversion_event": {
                    "type": "string",
                    "description": "Conversion event name (default '$form_submit').",
                },
                "include_conversion": {"type": "boolean", "description": "Include conversion event (default true)."},
                "include_error": {
                    "type": "boolean",
                    "description": "Include synthetic rage click, JS exception, and HTTP 500 (default false).",
                },
                "include_replay": {
                    "type": "boolean",
                    "description": "Include rrweb replay + console/network events (default true).",
                },
            },
        },
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
        requires_auth=True,
        handler=_tool_send_test_session,
    ),
    _ToolSpec(
        name="get_overview",
        title="Get Site KPIs & Overview",
        description=(
            "Pull high-level analytics for your Secret Key's site: sessions, unique visitors, pageviews, "
            "conversion rate, bounce rate, avg duration, prior-period comparison deltas, P75 Web Vitals, breakdowns, and recent sessions."
        ),
        input_schema={
            "type": "object",
            "properties": {
                **_FILTER_PROPS,
                "status": {
                    "type": "string",
                    "enum": [
                        "all",
                        "converted",
                        "rage",
                        "dead",
                        "error",
                        "broken",
                        "bounced",
                        "engaged",
                        "active",
                        "identified",
                    ],
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "description": "Max recent sessions returned (default 20).",
                },
            },
        },
        annotations=_RO_ANNOTATIONS,
        requires_auth=True,
        handler=_tool_get_overview,
    ),
    _ToolSpec(
        name="get_chart_data",
        title="Get Chart-Ready Analytics Series",
        description=(
            "Pull normalized chart-ready datasets scoped to your Secret Key: time-series (sessions, visitors, pageviews, converted with ISO timestamps), "
            "conversion funnel bars, top pages ranking, traffic channels & AI referrals, device/browser/OS/country shares, and 30m/60m realtime sparklines. "
            "Ideal for plotting charts, building React visualizations, or summarizing performance."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "chart_type": {
                    "type": "string",
                    "enum": list(VALID_CHART_TYPES),
                    "description": "Which chart dataset(s) to return (default 'all').",
                },
                "steps": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional funnel steps when chart_type is 'conversion_funnel' or 'all'.",
                },
                **_FILTER_PROPS,
            },
        },
        annotations=_RO_ANNOTATIONS,
        requires_auth=True,
        handler=_tool_get_chart_data,
    ),
    _ToolSpec(
        name="get_funnel_analysis",
        title="Analyze Conversion Funnels",
        description=(
            "Compute step-by-step conversion rates, dropoff counts/percentages, dropoff session IDs, median step gaps, "
            "and auto-discovered high-traffic funnel presets (`suggested_funnels`) for your Secret Key's site."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Ordered steps, e.g. ['path:/', 'path:/pricing', 'text:Sign up', 'event:$form_submit'].",
                },
                **_FILTER_PROPS,
            },
        },
        annotations=_RO_ANNOTATIONS,
        requires_auth=True,
        handler=_tool_get_funnel_analysis,
    ),
    _ToolSpec(
        name="get_user_flows",
        title="Get User Flows & Navigation Paths",
        description=(
            "Pull visitor navigation flows: entry pages, exit pages, page-to-page transitions, top multi-page paths, "
            "and step-by-step DAG nodes/links for Sankey or flow charts."
        ),
        input_schema={"type": "object", "properties": _FILTER_PROPS},
        annotations=_RO_ANNOTATIONS,
        requires_auth=True,
        handler=_tool_get_user_flows,
    ),
    _ToolSpec(
        name="get_breakage_and_errors",
        title="Get UX Breakage, Rage Clicks & JS Errors",
        description=(
            "Inspect UX friction and bugs for your Secret Key: top rage clicks, dead clicks, grouped JS exceptions, "
            "and broken session IDs you can inspect with `get_session_detail`."
        ),
        input_schema={
            "type": "object",
            "properties": {
                **_FILTER_PROPS,
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        },
        annotations=_RO_ANNOTATIONS,
        requires_auth=True,
        handler=_tool_get_breakage_and_errors,
    ),
    _ToolSpec(
        name="get_pages_and_vitals",
        title="Get Page Metrics & Web Vitals",
        description="Get per-page views, unique sessions, avg time on page, avg scroll depth %, exit rate, conversion rate, errors, and P75 Web Vitals (LCP, INP, CLS, FCP, TTFB).",
        input_schema={"type": "object", "properties": _FILTER_PROPS},
        annotations=_RO_ANNOTATIONS,
        requires_auth=True,
        handler=_tool_get_pages_and_vitals,
    ),
    _ToolSpec(
        name="get_referrers_and_ai_traffic",
        title="Get Traffic Sources & AI Referrals",
        description="Analyze referrer domains, acquisition channels (Direct, Organic Search, AI Answer Engines, Social, Email, Paid, Referral), conversion by source, and AI chatbot visits.",
        input_schema={"type": "object", "properties": _FILTER_PROPS},
        annotations=_RO_ANNOTATIONS,
        requires_auth=True,
        handler=_tool_get_referrers_and_ai_traffic,
    ),
    _ToolSpec(
        name="get_realtime_snapshot",
        title="Get Realtime Visitors & Live Activity",
        description="Get visitors active in the last 5 and 30 minutes, active pages, countries, minute-by-minute sparklines, and currently active sessions.",
        input_schema={
            "type": "object",
            "properties": {
                "secret_key": _SECRET_KEY_PROP,
                "site": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        },
        annotations=_RO_ANNOTATIONS,
        requires_auth=True,
        handler=_tool_get_realtime_snapshot,
    ),
    _ToolSpec(
        name="list_sessions",
        title="List Recorded Sessions",
        description="Search and filter recorded visitor sessions for your Secret Key by status (`converted`, `broken`, `rage`, `dead`, `error`, `bounced`, `engaged`, `active`, `identified`), device, browser, OS, or search text.",
        input_schema={
            "type": "object",
            "properties": {
                **_FILTER_PROPS,
                "status": {
                    "type": "string",
                    "enum": [
                        "all",
                        "converted",
                        "rage",
                        "dead",
                        "error",
                        "broken",
                        "bounced",
                        "engaged",
                        "active",
                        "identified",
                    ],
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "description": "Default 50."},
            },
        },
        annotations=_RO_ANNOTATIONS,
        requires_auth=True,
        handler=_tool_list_sessions,
    ),
    _ToolSpec(
        name="get_session_detail",
        title="Inspect Session Timeline & Replay Diagnostics",
        description=(
            "Inspect a specific session's complete chronological event timeline plus parsed rrweb session replay diagnostics "
            "(console logs/errors and fetch/XHR status codes/latencies) strictly scoped to your Secret Key."
        ),
        input_schema={
            "type": "object",
            "required": ["session_id"],
            "properties": {
                "secret_key": _SECRET_KEY_PROP,
                "session_id": {
                    "type": "string",
                    "description": "Session identifier from `list_sessions` or `send_test_session`.",
                },
                "include_recording_diagnostics": {
                    "type": "boolean",
                    "description": "Extract console logs and network requests from compressed rrweb chunks (default true).",
                },
                "include_raw_recording_events": {
                    "type": "boolean",
                    "description": "Also include up to 150 raw rrweb events (default false).",
                },
            },
        },
        annotations=_RO_ANNOTATIONS,
        requires_auth=True,
        handler=_tool_get_session_detail,
    ),
    _ToolSpec(
        name="get_live_events",
        title="Get Recent Event Stream",
        description="Pull the newest raw events ingested for your Secret Key's site, optionally filtered by timestamp cursor (`since`) or `event_name`.",
        input_schema={
            "type": "object",
            "properties": {
                "secret_key": _SECRET_KEY_PROP,
                "since": {
                    "type": "number",
                    "description": "Unix epoch timestamp cursor; return events strictly newer than this.",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "description": "Max events (default 100)."},
                "event_name": {
                    "type": "string",
                    "description": "Optional exact event name filter (e.g. '$pageview', '$exception').",
                },
            },
        },
        annotations=_RO_ANNOTATIONS,
        requires_auth=True,
        handler=_tool_get_live_events,
    ),
    _ToolSpec(
        name="delete_site",
        title="Delete Tracked Site",
        description="Remove the tracked site configuration associated with the provided Secret Key (`confirm: true` required).",
        input_schema={
            "type": "object",
            "required": ["confirm"],
            "properties": {
                "secret_key": _SECRET_KEY_PROP,
                "site": {"type": "string"},
                "confirm": {"type": "boolean", "description": "Must be true to delete the site."},
            },
        },
        annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False},
        requires_auth=True,
        handler=_tool_delete_site,
    ),
)

_TOOL_MAP: dict[str, _ToolSpec] = {tool.name: tool for tool in _TOOLS}

_STATIC_RESOURCES: tuple[dict[str, str], ...] = (
    {
        "uri": "hotspot://instructions/important",
        "name": "Hotspot Important Instructions",
        "description": "Required security guidelines, key rules, framework integration steps, testing workflow, and chart schemas.",
        "mimeType": "text/markdown",
    },
    {
        "uri": "hotspot://instructions/sdk-api",
        "name": "Hotspot JavaScript SDK & Privacy Reference",
        "description": "Complete reference of <script data-*> options, privacy masking attributes, and window.hotspot API methods.",
        "mimeType": "text/markdown",
    },
    {
        "uri": "hotspot://instructions/analytics-and-charts",
        "name": "Hotspot Analytics & Chart Schemas",
        "description": "Guide for querying chart-ready datasets, funnels, flows, breakage, and session diagnostics.",
        "mimeType": "text/markdown",
    },
    {
        "uri": "hotspot://site/current",
        "name": "Current Site Setup & Snapshot",
        "description": "Authenticated site's setup snippet, configuration, and live 24h KPI snapshot (requires Secret Key).",
        "mimeType": "application/json",
    },
)

_RESOURCE_TEMPLATES: tuple[dict[str, str], ...] = (
    {
        "uriTemplate": "hotspot://instructions/{topic}",
        "name": "Hotspot Instructions Section",
        "description": "Read a specific Hotspot instruction section (all, security, integration, sdk, testing, charts).",
        "mimeType": "text/markdown",
    },
)

_PROMPTS: tuple[dict[str, Any], ...] = (
    {
        "name": "install-hotspot",
        "title": "Install Hotspot in Project",
        "description": "Step-by-step instructions for an AI coding assistant to add Hotspot tracking safely to a web app and verify it.",
        "arguments": [
            {
                "name": "framework",
                "description": "Target framework (html, nextjs_app, nextjs_pages, react_vite, sveltekit, astro, django).",
                "required": False,
            },
            {
                "name": "domain",
                "description": "Website hostname to track (optional if already registered).",
                "required": False,
            },
        ],
    },
    {
        "name": "test-and-verify-session",
        "title": "Test & Verify Hotspot Session",
        "description": "Verify live telemetry ingestion or send a synthetic test session and inspect its timeline, console, and network diagnostics.",
        "arguments": [
            {
                "name": "session_id",
                "description": "Optional existing session_id to inspect directly.",
                "required": False,
            },
        ],
    },
    {
        "name": "visualize-site-analytics",
        "title": "Pull Data & Build Analytics Charts",
        "description": "Pull scoped time-series, funnel, page, channel, and device metrics via MCP to plot charts or create an executive report.",
        "arguments": [
            {"name": "hours", "description": "Lookback hours (e.g. 24, 168, 720, or 0 for all).", "required": False},
            {
                "name": "focus",
                "description": "Focus area (overview, funnels, traffic, retention/pages, realtime).",
                "required": False,
            },
        ],
    },
    {
        "name": "debug-dropoffs-and-ux",
        "title": "Debug Dropoffs, Rage Clicks & JS Errors",
        "description": "Investigate conversion dropoffs, rage clicks, dead clicks, JS errors, and failing network calls across recent sessions.",
        "arguments": [
            {"name": "hours", "description": "Lookback hours (default 24).", "required": False},
        ],
    },
)


def _render_prompt(service: HotspotService, name: str, args: Mapping[str, Any]) -> dict[str, Any]:
    framework = str(args.get("framework") or "html")
    domain = str(args.get("domain") or "")
    hours = str(args.get("hours") or "168")
    session_id = str(args.get("session_id") or "")
    focus = str(args.get("focus") or "overview")

    if name == "install-hotspot":
        reg_step = (
            f"1. Inspect the codebase to detect its framework (`{framework}`), then call `get_site_setup(framework='{framework}')` "
            "(or `register_site(domain='...')` if the user has not registered this domain yet)."
            if not domain
            else f"1. Call `get_site_setup(framework='{framework}')` (or `register_site(domain='{domain}', framework='{framework}')` if setting up fresh)."
        )
        body = (
            "You are integrating Hotspot telemetry into the user's application.\n"
            "First read `get_important_instructions` (topic='all').\n"
            f"{reg_step}\n"
            "2. Insert ONLY the public `api_key` (`hs_live_...`) inside the frontend `<script>` snippet — NEVER put `hs_sec_...` into frontend source files.\n"
            "3. Check CSP headers if present and allow the Hotspot `site_url` in `script-src` and `connect-src`.\n"
            "4. If the app has user authentication or key conversion actions, wire `window.hotspot?.identify(...)` and `window.hotspot?.capture(...)` (never `.track`).\n"
            "5. After adding the snippet, run the app or call `send_test_session` + `verify_connection` to prove telemetry arrives cleanly."
        )
        return {
            "description": "Install Hotspot safely into a codebase and verify telemetry.",
            "messages": [{"role": "user", "content": {"type": "text", "text": body}}],
        }

    if name == "test-and-verify-session":
        target = (
            f"Inspect `session_id='{session_id}'` with `get_session_detail(session_id='{session_id}', include_recording_diagnostics=True)`."
            if session_id
            else (
                "1. Call `verify_connection` to check if real browser events are already arriving.\n"
                "2. Call `send_test_session(include_error=True, include_replay=True)` or test the live app in a browser.\n"
                "3. Call `list_sessions` and `get_session_detail(include_recording_diagnostics=True)` on the newest session.\n"
                "4. Summarize all captured `$pageview`, click, `$web_vitals`, conversion, console, and network diagnostics."
            )
        )
        return {
            "description": "Verify Hotspot telemetry ingestion and replay diagnostics.",
            "messages": [{"role": "user", "content": {"type": "text", "text": target}}],
        }

    if name == "visualize-site-analytics":
        body = (
            f"Pull analytics for the user's Hotspot Secret Key over the last `{hours}` hours (focus: `{focus}`):\n"
            "1. Call `get_important_instructions(topic='charts')` if you need schema details.\n"
            f"2. Call `get_chart_data(chart_type='all', hours={hours})` and `get_funnel_analysis(hours={hours})`.\n"
            "3. Present the exact metrics and render clear visualizations/charts (time-series traffic vs conversions, conversion funnel bars with dropoffs, top pages, traffic channels & AI referrals, and device/country breakdowns) — using exact observed numbers only."
        )
        return {
            "description": "Pull Hotspot datasets and render charts.",
            "messages": [{"role": "user", "content": {"type": "text", "text": body}}],
        }

    if name == "debug-dropoffs-and-ux":
        body = (
            f"Diagnose conversion dropoffs and broken UX flows over the last `{hours}` hours:\n"
            f"1. Call `get_funnel_analysis(hours={hours})` and `get_breakage_and_errors(hours={hours})`.\n"
            f"2. Call `list_sessions(hours={hours}, status='broken', limit=10)`.\n"
            "3. For the top broken or dropoff `session_id`s, call `get_session_detail(session_id=..., include_recording_diagnostics=True)`.\n"
            "4. Correlate rage clicks, dead clicks, JS exceptions, and 4xx/5xx network responses to pinpoint concrete fixes in the app codebase."
        )
        return {
            "description": "Root-cause UX friction and dropoff sessions.",
            "messages": [{"role": "user", "content": {"type": "text", "text": body}}],
        }

    raise McpProtocolError(_ERROR_INVALID_PARAMS, f"Unknown prompt '{name}'.")


# ---------------------------------------------------------------------------
# JSON-RPC Dispatcher
# ---------------------------------------------------------------------------


def _serialize_tool(tool: _ToolSpec) -> dict[str, Any]:
    return {
        "name": tool.name,
        "title": tool.title,
        "description": tool.description,
        "inputSchema": tool.input_schema,
        "annotations": tool.annotations,
    }


def _jsonrpc_result(msg_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _jsonrpc_error(msg_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": msg_id, "error": err}


def _handle_single_message(
    msg: Any,
    service: HotspotService,
    *,
    headers: Mapping[str, str] | None = None,
    query_params: Mapping[str, str] | None = None,
    default_secret_key: str | None = None,
) -> dict[str, Any] | None:
    if not isinstance(msg, dict):
        return _jsonrpc_error(None, _ERROR_INVALID_REQUEST, "JSON-RPC message must be a JSON object.")

    msg_id = msg.get("id")
    is_notification = "id" not in msg
    method = msg.get("method")
    if msg.get("jsonrpc") != "2.0" or not isinstance(method, str) or not method:
        return (
            None if is_notification else _jsonrpc_error(msg_id, _ERROR_INVALID_REQUEST, "Invalid JSON-RPC 2.0 request.")
        )

    params = msg.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return None if is_notification else _jsonrpc_error(msg_id, _ERROR_INVALID_PARAMS, "`params` must be an object.")

    # Notifications do not return a JSON-RPC response object.
    if method.startswith("notifications/") or is_notification:
        return None

    try:
        # Validate any HTTP credential immediately so invalid bearer tokens always fail early.
        if headers or query_params:
            resolve_mcp_auth(service, headers=headers, query_params=query_params, require=False)

        if method == "initialize":
            client_version = str(params.get("protocolVersion") or "").strip()
            protocol_version = (
                client_version if client_version in SUPPORTED_PROTOCOL_VERSIONS else DEFAULT_PROTOCOL_VERSION
            )
            return _jsonrpc_result(
                msg_id,
                {
                    "protocolVersion": protocol_version,
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "resources": {"subscribe": False, "listChanged": False},
                        "prompts": {"listChanged": False},
                        "logging": {},
                    },
                    "serverInfo": {
                        "name": "hotspot",
                        "title": "Hotspot Telemetry & Replay MCP",
                        "version": __version__,
                    },
                    "instructions": _important_instructions_text(service, topic="all"),
                },
            )

        if method == "ping":
            return _jsonrpc_result(msg_id, {})

        if method == "logging/setLevel":
            return _jsonrpc_result(msg_id, {})

        if method == "tools/list":
            return _jsonrpc_result(msg_id, {"tools": [_serialize_tool(t) for t in _TOOLS]})

        if method == "tools/call":
            tool_name = str(params.get("name") or "").strip()
            tool = _TOOL_MAP.get(tool_name)
            if tool is None:
                return _jsonrpc_error(msg_id, _ERROR_INVALID_PARAMS, f"Unknown tool '{tool_name}'.")
            arguments = params.get("arguments")
            if arguments is None:
                arguments = {}
            if not isinstance(arguments, dict):
                return _jsonrpc_error(msg_id, _ERROR_INVALID_PARAMS, "`arguments` must be an object.")

            arg_key = _extract_secret_arg(arguments) or default_secret_key
            try:
                auth = resolve_mcp_auth(
                    service,
                    headers=headers,
                    query_params=query_params,
                    arg_secret_key=arg_key,
                    require=tool.requires_auth,
                )
                payload = tool.handler(service, arguments, auth)
            except (McpAuthError, McpProtocolError) as exc:
                return _jsonrpc_result(
                    msg_id,
                    {
                        "content": [{"type": "text", "text": str(exc)}],
                        "isError": True,
                    },
                )
            return _jsonrpc_result(
                msg_id,
                {
                    "content": [{"type": "text", "text": json.dumps(payload, indent=2, default=str)}],
                    "structuredContent": payload,
                    "isError": False,
                },
            )

        if method == "resources/list":
            return _jsonrpc_result(msg_id, {"resources": list(_STATIC_RESOURCES)})

        if method == "resources/templates/list":
            return _jsonrpc_result(msg_id, {"resourceTemplates": list(_RESOURCE_TEMPLATES)})

        if method == "resources/read":
            uri = str(params.get("uri") or "").strip()
            if not uri:
                return _jsonrpc_error(msg_id, _ERROR_INVALID_PARAMS, "`uri` is required.")
            if uri.startswith("hotspot://instructions/"):
                topic = uri.removeprefix("hotspot://instructions/").strip("/") or "all"
                mapped = {"sdk-api": "sdk", "analytics-and-charts": "charts"}.get(topic, topic)
                return _jsonrpc_result(
                    msg_id,
                    {
                        "contents": [
                            {
                                "uri": uri,
                                "mimeType": "text/markdown",
                                "text": _important_instructions_text(service, topic=mapped),
                            }
                        ]
                    },
                )
            if uri == "hotspot://site/current":
                auth = resolve_mcp_auth(
                    service,
                    headers=headers,
                    query_params=query_params,
                    arg_secret_key=default_secret_key,
                    require=True,
                )
                assert auth is not None
                setup_data = _tool_get_site_setup(service, {}, auth)
                overview_data = _tool_get_overview(service, {"hours": 24, "limit": 5}, auth)
                return _jsonrpc_result(
                    msg_id,
                    {
                        "contents": [
                            {
                                "uri": uri,
                                "mimeType": "application/json",
                                "text": json.dumps(
                                    {"setup": setup_data, "overview_24h": overview_data},
                                    indent=2,
                                    default=str,
                                ),
                            }
                        ]
                    },
                )
            return _jsonrpc_error(msg_id, _ERROR_INVALID_PARAMS, f"Resource not found: '{uri}'.")

        if method == "prompts/list":
            return _jsonrpc_result(msg_id, {"prompts": list(_PROMPTS)})

        if method == "prompts/get":
            prompt_name = str(params.get("name") or "").strip()
            prompt_args = params.get("arguments") or {}
            if not isinstance(prompt_args, dict):
                return _jsonrpc_error(msg_id, _ERROR_INVALID_PARAMS, "`arguments` must be an object.")
            return _jsonrpc_result(msg_id, _render_prompt(service, prompt_name, prompt_args))

        return _jsonrpc_error(msg_id, _ERROR_METHOD_NOT_FOUND, f"Method '{method}' not found.")

    except McpAuthError as exc:
        return _jsonrpc_error(msg_id, _ERROR_UNAUTHORIZED, str(exc))
    except McpProtocolError as exc:
        return _jsonrpc_error(msg_id, exc.code, exc.message, exc.data)
    except Exception as exc:  # noqa: BLE001
        return _jsonrpc_error(msg_id, _ERROR_INTERNAL, f"Internal MCP server error: {exc}")


def handle_mcp_http_payload(
    body: bytes,
    *,
    service: HotspotService | None = None,
    headers: Mapping[str, str] | None = None,
    query_params: Mapping[str, str] | None = None,
    default_secret_key: str | None = None,
) -> tuple[int, Any]:
    """Process a raw HTTP JSON-RPC payload and return `(http_status, response_object_or_None)`.

    Returns HTTP 401 immediately when an explicit HTTP header/query credential is invalid,
    and HTTP 202 with `None` when every incoming item is a JSON-RPC notification.
    """
    svc = service or get_service()

    try:
        resolve_mcp_auth(svc, headers=headers, query_params=query_params, require=False)
    except McpAuthError as exc:
        return 401, _jsonrpc_error(None, _ERROR_UNAUTHORIZED, str(exc))

    try:
        decoded = json.loads(body.decode("utf-8")) if body else None
    except (UnicodeDecodeError, ValueError):
        return 400, _jsonrpc_error(None, _ERROR_PARSE, "Invalid JSON payload.")

    if isinstance(decoded, list):
        if not decoded:
            return 400, _jsonrpc_error(None, _ERROR_INVALID_REQUEST, "Empty JSON-RPC batch is invalid.")
        responses = []
        for item in decoded:
            resp = _handle_single_message(
                item,
                svc,
                headers=headers,
                query_params=query_params,
                default_secret_key=default_secret_key,
            )
            if resp is not None:
                responses.append(resp)
        if not responses:
            return 202, None
        return 200, responses

    resp = _handle_single_message(
        decoded,
        svc,
        headers=headers,
        query_params=query_params,
        default_secret_key=default_secret_key,
    )
    if resp is None:
        return 202, None
    return 200, resp


def mcp_discovery_document(service: HotspotService) -> dict[str, Any]:
    """Public metadata shown on plain browser `GET /mcp` requests."""
    base = service.config.site_url.rstrip("/")
    return {
        "name": "hotspot",
        "title": "Hotspot Telemetry & Replay MCP",
        "version": __version__,
        "protocol_versions": list(SUPPORTED_PROTOCOL_VERSIONS),
        "transports": {
            "streamable_http": f"{base}/mcp",
            "legacy_sse": f"{base}/mcp/sse",
            "stdio": "python -m products.hotspot.mcp",
        },
        "auth": {
            "required_for_site_data": True,
            "methods": [
                "Authorization: Bearer <YOUR_HOTSPOT_SECRET_KEY>",
                "X-Hotspot-Secret-Key: <YOUR_HOTSPOT_SECRET_KEY>",
                f"{base}/mcp?secret_key=<YOUR_HOTSPOT_SECRET_KEY>",
                "Pass `secret_key` argument per tool call",
            ],
            "note": "Always authenticate MCP with your private Secret Key (hs_sec_...), never with the public tracking key (hs_live_...).",
        },
        "tools_count": len(_TOOLS),
        "tools": [t.name for t in _TOOLS],
        "resources": [r["uri"] for r in _STATIC_RESOURCES],
        "prompts": [p["name"] for p in _PROMPTS],
        "example_client_config": _mcp_config_snippet(service),
    }


def session_id_matches(expected: str, provided: str | None) -> bool:
    """Constant-time comparison helper for MCP session tokens."""
    if not provided:
        return False
    return hmac.compare_digest(expected.encode("utf-8"), provided.encode("utf-8"))


def run_stdio_server(
    *,
    service: HotspotService | None = None,
    default_secret_key: str | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> None:
    """Run an interactive MCP stdio JSON-RPC loop (newline-delimited JSON)."""
    svc = service or get_service()
    in_stream = stdin or sys.stdin
    out_stream = stdout or sys.stdout

    for raw_line in in_stream:
        line = raw_line.strip()
        if not line:
            continue
        _status, response = handle_mcp_http_payload(
            line.encode("utf-8"),
            service=svc,
            default_secret_key=default_secret_key,
        )
        if response is not None:
            out_stream.write(json.dumps(response, default=str) + "\n")
            out_stream.flush()
