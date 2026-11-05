# Hotspot

Hotspot is self-hosted product telemetry. One script tag records what visitors do on your site, your server stores it, and a dashboard shows sessions, a replay timeline, funnels, paths and friction. No data leaves your infrastructure.

## What the script records

| Event | When | Properties worth knowing |
| --- | --- | --- |
| `$pageview` | On load and on every single-page-app route change | `$pathname`, `$title`, `$href`, viewport and screen size, referrer, UTM parameters |
| `$pageleave` | When the visitor leaves a page or the tab is hidden | `$pathname`, `$duration_seconds`, `$scroll_depth_percent` |
| `$autocapture` | Every click | `$el_selector`, `$el_text`, `$el_tag_name`, `$el_href`, `$is_dead_click`, `$client_x`/`$client_y` (viewport), `$page_x`/`$page_y` (page) |
| `$rageclick` | Three or more clicks within 1.2 s and 40 px | The same click properties |
| `$form_submit` | A form is submitted | `$form_id`, `$form_name`, `$form_action`, `$form_method` |
| `$exception` | An uncaught error or unhandled promise rejection | `$exception_message`, `$exception_type`, `$exception_stack` |
| `$identify`, `$set` | `hotspot.identify()` / `hotspot.setPersonProperties()` | `$set` with `email`, `name`, `company` |
| anything else | `hotspot.capture('name', {...})` | Your properties |

The server adds `$browser`, `$browser_version`, `$os` and `$device_type` from the User-Agent header, and `$country` when a proxy sends `CF-IPCountry`, `X-Country-Code` or `CloudFront-Viewer-Country`. IP addresses are not stored. Input and textarea values are never recorded.

A dead click is a click on something that is not interactive (no link, button, form control, role, handler or pointer cursor within five ancestors). Visitor and session ids live in `localStorage` with a first-party cookie fallback; a session ends after 30 minutes without events.

## Layout

```text
products/hotspot/
├── backend/
│   ├── apps.py          Django app registration
│   ├── urls.py, views.py  Django entrypoints (thin)
│   ├── service.py       Framework-neutral application layer used by both servers
│   ├── config.py        Environment variables -> HotspotConfig
│   ├── ingestion.py     Validates and bounds a capture batch; useragent.py parses the UA
│   ├── store.py         Session summaries + events in Redis (memory fallback)
│   ├── analytics.py     Overview: metrics, funnel, paths, friction, pages, referrers, errors
│   ├── auth.py          Dashboard password -> signed cookie token
│   ├── security.py      CSP, CORS and cache headers
│   ├── templating.py    Placeholder renderer used by the standalone server
│   └── test/
├── sdk/hotspot.js       The script visitors load
├── templates/hotspot/   landing.html, dashboard.html, demo.html, login.html
└── standalone/          Starlette server, Dockerfile, docker-compose.yml
```

## Run it

### Standalone (Python 3.11+)

```bash
pip install -r products/hotspot/standalone/requirements.txt
HOTSPOT_DASHBOARD_PASSWORD=change-me HOTSPOT_API_KEYS=hs_my_site python products/hotspot/standalone/server.py
```

Or with Docker, which also starts Redis so data survives restarts:

```bash
cd products/hotspot/standalone && docker compose up -d
```

### Inside the PostHog-based stack

`products.hotspot` is a Django app. `posthog/urls.py` includes `products.hotspot.backend.urls`, so the pages and API are served by the main web process. `./bin/hotspot up` starts the full Docker stack from `docker-compose.hotspot.yml`; set the `HOTSPOT_*` variables in `.env.hotspot`.

## Configuration

| Variable | Default | What it does |
| --- | --- | --- |
| `HOTSPOT_SITE_URL` (or `SITE_URL`) | `http://localhost:8000` | Public origin, used in install snippets and the sample site |
| `HOTSPOT_API_KEYS` | unset | Comma-separated keys the capture endpoint accepts. Unset accepts any key, which is fine locally and wrong in production |
| `HOTSPOT_DASHBOARD_PASSWORD` | unset | Password for `/dashboard/` and the API. Unset leaves the dashboard open |
| `HOTSPOT_SECRET_KEY` (or `SECRET_KEY`) | random per process | Signs the dashboard cookie. Set it so sign-ins survive restarts and are shared by workers |
| `REDIS_URL` | unset | Redis connection string. Unset keeps data in process memory, which is lost on restart and not shared between workers |
| `HOTSPOT_CONVERSION_EVENTS` | `$form_submit` | Comma-separated event names that mark a session as converted |
| `HOTSPOT_SESSION_TTL_SECONDS` | 7 days | How long sessions are kept |
| `HOTSPOT_MAX_EVENTS` | 5000 | Size of the live event feed |
| `HOTSPOT_MAX_SESSION_EVENTS` | 2000 | Events kept per session for replay |
| `HOTSPOT_MAX_BATCH_SIZE` / `HOTSPOT_MAX_BODY_BYTES` | 500 / 1 MiB | Capture request limits |
| `HOTSPOT_DEMO_ENABLED` | `1` | Set `0` to turn off the sample site at `/demo/` |

## Install the script

```html
<script src="https://YOUR_HOTSPOT_HOST/hotspot.js" data-api-key="YOUR_API_KEY" defer></script>
```

Attributes: `data-api-host` (defaults to the host the script came from), `data-autocapture="false"`, `data-capture-errors="false"`, `data-capture-pageview="false"`, `data-debug="true"` (console logging), `data-debug-hud="true"` (an on-page overlay of your own clicks, toggled with Alt+H; also enabled by `?hotspot_debug_hud=1`).

JavaScript API on `window.hotspot`: `capture(name, props)`, `identify(id, {email, name, company})`, `setPersonProperties(props)`, `register(props)` (sent with every event), `reset()`, `optOut()`, `optIn()`, `hasOptedOut()`, `getDistinctId()`, `getSessionId()`, `on(eventName, callback)`, `flush()`, `debug.show()`, `debug.hide()`.

Give your AI coding assistant this instruction to add it to a project:

> Add `<script src="https://YOUR_HOTSPOT_HOST/hotspot.js" data-api-key="YOUR_API_KEY" defer></script>` to the `<head>` of the root layout (Next.js `app/layout.tsx`, Vite `index.html`, SvelteKit `app.html`, Astro layout). Do not add other analytics tags. Call `window.hotspot.identify(userId, { email, name })` after sign-in.

## HTTP API

Pages: `/` landing, `/demo/` sample site, `/dashboard/` (redirects to `/dashboard/login/` when a password is set), `/hotspot.js`, `/llms.txt`.

The dashboard API accepts the sign-in cookie or `Authorization: Bearer <HOTSPOT_DASHBOARD_PASSWORD>`.

### `POST /hotspot/batch/`

Public, CORS `*`. Body (JSON, sent as `text/plain` to avoid a preflight):

```json
{"api_key": "hs_my_site", "sent_at": "...", "batch": [{"event": "$pageview", "timestamp": "2026-11-05T10:00:00Z", "properties": {"distinct_id": "...", "$session_id": "...", "$pathname": "/"}}]}
```

Answers `{"status": 1, "received": n}`, `401` for a missing or unknown key, `413` for an oversized body or batch.

### `GET /hotspot/api/overview/`

Query parameters: `hours` (0 = all), `status` (`all|converted|rage|error|bounced|engaged|active|identified|dead`), `device_type`, `browser`, `os`, `search`, `limit`, `funnel` (steps separated by `|`, each `path:/pricing`, `event:$form_submit`, `click:#selector`, or `text:Buy now`; a trailing `*` on a path or selector is a prefix match), `conversion_event`.

`hours`, `device_type`, `browser` and `os` define the scope every section is computed over; `status` and `search` only filter the `sessions` list. `breakdowns` is the exception: it is computed on the time range alone, so filter menus keep every device, browser and OS that exists. `scope.sessions_stored` counts every session the server holds and `scope.sessions_in_range` the ones inside the time range, so an empty result can say whether data exists at all.

```json
{
  "status": 1, "generated_at": 1762336800.0,
  "scope": {"hours": 24, "device_type": "all", "browser": "all", "os": "all", "status": "all", "search": "", "sessions_analyzed": 42, "sessions_in_range": 42, "sessions_stored": 300},
  "setup": {"version": "3.0.0", "site_url": "...", "sdk_url": ".../hotspot.js", "api_key": "hs_my_site", "ingestion_keys_configured": true, "dashboard_protected": true, "demo_enabled": true, "storage": "redis", "conversion_events": ["$form_submit"], "session_ttl_seconds": 604800},
  "metrics": {"sessions": 42, "filtered_sessions": 42, "active_sessions": 3, "identified_sessions": 5, "pageviews": 120, "clicks": 300, "rage_clicks": 4, "dead_clicks": 11, "form_submits": 6, "errors": 2, "converted_sessions": 6, "conversion_rate": 14.3, "bounce_rate": 31.0, "avg_duration_seconds": 84.2, "avg_scroll_depth": 61, "ai_referral_sessions": 1},
  "sessions": [{"session_id": "...", "distinct_id": "...", "label": "Visitor a1b2c3", "identity": {"email": "..."}, "status": "rage", "first_seen": 0, "last_seen": 0, "seconds_ago": 12, "duration_seconds": 40, "active_seconds": 30, "event_count": 9, "pageview_count": 2, "click_count": 4, "rage_click_count": 1, "dead_click_count": 1, "form_submit_count": 0, "error_count": 0, "custom_event_count": 0, "pages": ["/", "/pricing"], "entry_page": "/", "exit_page": "/pricing", "entry_url": "https://...", "entry_title": "...", "referrer": "...", "referrer_host": "google.com", "is_ai_referral": false, "ai_source": null, "utm": {}, "browser": "Chrome", "browser_version": "130.0.0.0", "os": "macOS", "device_type": "desktop", "country": "DE", "viewport_width": 1440, "viewport_height": 900, "language": "en-US", "timezone": "Europe/Berlin", "max_scroll_depth": 72, "converted": false, "conversion_event": null, "last_event": "$pageleave"}],
  "funnel": {"steps": [{"index": 1, "spec": "event:$pageview", "kind": "event", "value": "$pageview", "label": "Viewed a page", "count": 42, "pct_of_first": 100.0, "conv_from_prev": 100.0, "dropoff_from_prev": 0.0, "dropoff_count": 0, "median_seconds_to_next": 12.0}], "sessions_analyzed": 42, "conversion_rate": 14.3},
  "paths": {"entry_pages": [{"pathname": "/", "count": 30, "share": 71.4}], "exit_pages": [], "transitions": [{"from": "/", "to": "/pricing", "count": 12, "share": 40.0}], "top_paths": [{"pages": ["/", "/pricing"], "count": 12, "share": 28.6, "converted": 2}]},
  "friction": {"rage_clicks": [{"pathname": "/pricing", "selector": "#plan-enterprise", "text": "Enterprise", "count": 4, "sessions": 3, "last_seen": 0}], "dead_clicks": []},
  "pages": [{"pathname": "/", "views": 50, "sessions": 40, "avg_seconds": 31.2, "avg_scroll_depth": 58, "exits": 12, "exit_rate": 30.0}],
  "referrers": {"sources": [{"host": "direct", "sessions": 20, "share": 47.6, "converted": 3, "conversion_rate": 15.0}], "ai_sessions": 1, "ai_sources": [{"source": "ChatGPT", "sessions": 1}]},
  "errors": [{"message": "x is not a function", "type": "TypeError", "count": 2, "sessions": 2, "last_seen": 0, "pathname": "/checkout"}],
  "activity": {"events_per_minute": [0, 0, 3], "sessions_per_hour": [0, 2, 1]},
  "breakdowns": {"device_type": {"desktop": 30, "mobile": 12}, "browser": {"Chrome": 25}, "os": {"macOS": 20}, "country": {"DE": 10}}
}
```

Null means unknown. Nothing is invented when data is missing.

### `GET /hotspot/api/sessions/<session_id>/`

`{"status": 1, "session": {...same shape as a sessions row...}, "events": [{"event": "$autocapture", "timestamp": 1762336800.5, "properties": {...}}]}` with events in time order. `404` when the session is unknown or expired.

### `GET /hotspot/api/events/?since=<unix seconds>&limit=100`

`{"status": 1, "now": 0, "events": [{"event": "...", "timestamp": 0, "session_id": "...", "distinct_id": "...", "properties": {...}}]}`, newest first.

## Templates

The three pages are single files under `templates/hotspot/`. Both servers render them: Django with its template engine and the standalone server with `templating.render_string`, so they use only `{{ name }}` placeholders and nothing else from Django's template language. Available placeholders: `page_title`, `site_url`, `sdk_url`, `api_key`, `csp_nonce`, `version`, `dashboard_protected` (`"1"` or empty), `demo_enabled`, `storage` (`redis` or `memory`), and `error` on the login page.

Every page is served with a content security policy: inline scripts must carry `nonce="{{ csp_nonce }}"`, inline event handler attributes do not run, scripts load only from the same origin, styles from the same origin or Google Fonts, and `connect-src` is the same origin. The dashboard may frame pages (`frame-src` is open) so the replay can show a page preview.

## Privacy and retention

Hotspot stores event properties, a random visitor id, a session id and whatever you pass to `identify()`. It does not store IP addresses or form input values. Sessions expire after `HOTSPOT_SESSION_TTL_SECONDS`. Visitors can opt out with `hotspot.optOut()`.

## Tests

```bash
uv run pytest products/hotspot/backend/test
```
