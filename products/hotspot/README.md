# Hotspot

Hotspot is self-hosted product telemetry. One script tag records what visitors do on your site, your server stores it, and a dashboard replays sessions and shows an overview with trends, a realtime map, the flow between pages, funnels and breakage. No data leaves your infrastructure.

## What the script records

| Event | When | Properties worth knowing |
| --- | --- | --- |
| `$pageview` | On load and on every single-page-app route change | `$pathname`, `$title`, `$href`, viewport and screen size, referrer, UTM parameters |
| `$pageleave` | When the visitor leaves a page or the tab is hidden | `$pathname`, `$duration_seconds`, `$scroll_depth_percent` |
| `$web_vitals` | Once per page, just before `$pageleave` | `$pathname`, `$lcp_ms`, `$fcp_ms`, `$ttfb_ms` (first load of the document only), `$cls`, `$inp_ms` (per path) |
| `$autocapture` | Every click | `$el_selector`, `$el_text`, `$el_tag_name`, `$el_href`, `$is_dead_click`, `$client_x`/`$client_y` (viewport), `$page_x`/`$page_y` (page) |
| `$rageclick` | Three or more clicks within 1.2 s and 40 px | The same click properties |
| `$form_submit` | A form is submitted | `$form_id`, `$form_name`, `$form_action`, `$form_method` |
| `$exception` | An uncaught error or unhandled promise rejection | `$exception_message`, `$exception_type`, `$exception_stack` |
| `$identify`, `$set` | `hotspot.identify()` / `hotspot.setPersonProperties()` | `$set` with `email`, `name`, `company` |
| anything else | `hotspot.capture('name', {...})` | Your properties |

The server adds `$browser`, `$browser_version`, `$os` and `$device_type` from the User-Agent header, and `$country` when a proxy sends `CF-IPCountry`, `X-Country-Code` or `CloudFront-Viewer-Country`. IP addresses are not stored. Analytics events never carry input values.

A dead click is a click on something that is not interactive (no link, button, form control, role, handler or pointer cursor within five ancestors). Visitor and session ids live in `localStorage` with a first-party cookie fallback; a session ends after 30 minutes without events.

### Session replay

Next to the events, the script records the page itself with [rrweb](https://github.com/rrweb-io/rrweb): one snapshot of the DOM, then every mutation, input, scroll and mouse movement. The dashboard rebuilds the page from that recording in a sandboxed frame that runs no scripts, so you see what the visitor saw, including what they typed. The recorder (`/hotspot/assets/recorder.js`, about 24 KB gzipped) is a separate file loaded on demand, so pages with replay turned off never download it.

Masking happens in the browser; masked values never leave the page.

- Password fields and payment fields (`autocomplete="cc-*"`, `one-time-code`, `current-password`, `new-password`) are always masked.
- `data-hotspot-mask` on an element masks its text and every input value inside it (`.hs-mask` does the same).
- `data-hotspot-block` replaces an element with an empty box of the same size (`.hs-block` does the same).
- `data-hotspot-ignore` skips an element's input events.
- `data-mask-inputs="all"` on the script tag masks every input value; `data-mask-text="true"` masks every text node; `data-replay="false"` turns recording off.

Console output (`log`, `info`, `warn`, `error`, `debug`, plus uncaught errors) and network requests (method, URL without credentials, status, duration; never bodies or headers) ride along in the recording as rrweb custom events and show up in the player's Console and Network tabs. Both can be turned off with `data-capture-console="false"` and `data-capture-network="false"`; `data-capture-web-vitals="false"` turns off the `$web_vitals` event.

Recordings are uploaded in gzip-compressed chunks every 5 seconds, right after a snapshot, and when the page is hidden or closed. A page that is closed can only send about 64 KB on its way out, so the last few seconds of a session can be missing. Each session keeps up to `HOTSPOT_MAX_RECORDING_BYTES` of compressed recording; after that the dashboard marks the recording as cut short. A session that expires while the tab stays open starts a new recording with its own snapshot.

## Layout

```text
products/hotspot/
├── backend/
│   ├── apps.py          Django app registration
│   ├── urls.py, views.py  Django entrypoints (thin)
│   ├── service.py       Framework-neutral application layer used by both servers
│   ├── config.py        Environment variables -> HotspotConfig
│   ├── ingestion.py     Validates and bounds a capture batch or a recording chunk; useragent.py parses the UA
│   ├── store.py         Session summaries, events and recording chunks in Redis (memory fallback)
│   ├── analytics.py     Overview: metrics, trend, funnel, paths and flow, friction, pages, web vitals, referrers and channels, errors, realtime
│   ├── auth.py          Dashboard password -> signed cookie token
│   ├── security.py      CSP, CORS and cache headers
│   ├── templating.py    Placeholder renderer used by the standalone server
│   └── test/
├── sdk/hotspot.js       The script visitors load
├── vendor/              rrweb recorder and player bundles (see vendor/README.md)
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
| `HOTSPOT_API_KEYS` | unset | Comma-separated keys the capture endpoints accept. Unset accepts any key, which is fine locally and wrong in production |
| `HOTSPOT_DASHBOARD_PASSWORD` | unset | Password for `/dashboard/` and the API. Unset leaves the dashboard open |
| `HOTSPOT_SECRET_KEY` (or `SECRET_KEY`) | random per process | Signs the dashboard cookie. Set it so sign-ins survive restarts and are shared by workers |
| `REDIS_URL` | unset | Redis connection string. Unset keeps data in process memory, which is lost on restart and not shared between workers |
| `HOTSPOT_CONVERSION_EVENTS` | `$form_submit` | Comma-separated event names that mark a session as converted |
| `HOTSPOT_SESSION_TTL_SECONDS` | 7 days | How long sessions, their events and their recordings are kept |
| `HOTSPOT_MAX_EVENTS` | 5000 | Size of the live event feed |
| `HOTSPOT_MAX_SESSION_EVENTS` | 2000 | Events kept per session for the timeline |
| `HOTSPOT_MAX_BATCH_SIZE` / `HOTSPOT_MAX_BODY_BYTES` | 500 / 1 MiB | Event capture request limits |
| `HOTSPOT_REPLAY_ENABLED` | `1` | Set `0` to refuse recordings; the script stops recording when the server answers 403 |
| `HOTSPOT_MAX_RECORDING_BYTES` | 10 MiB | Compressed recording kept per session. A minute of browsing is usually well under 1 MiB |
| `HOTSPOT_MAX_RECORDING_BODY_BYTES` | 8 MiB | Largest recording chunk accepted, measured after decompression |
| `HOTSPOT_DEMO_ENABLED` | `1` | Set `0` to turn off the sample site at `/demo/` |

## Install the script

```html
<script src="https://YOUR_HOTSPOT_HOST/hotspot.js" data-api-key="YOUR_API_KEY" defer></script>
```

Attributes: `data-api-host` (defaults to the host the script came from), `data-autocapture="false"`, `data-capture-errors="false"`, `data-capture-pageview="false"`, `data-replay="false"`, `data-mask-inputs="all"`, `data-mask-text="true"`, `data-capture-console="false"`, `data-capture-network="false"`, `data-capture-web-vitals="false"`, `data-debug="true"` (console logging), `data-debug-hud="true"` (an on-page overlay of your own clicks, toggled with Alt+H; also enabled by `?hotspot_debug_hud=1`).

JavaScript API on `window.hotspot`: `capture(name, props)`, `identify(id, {email, name, company})`, `setPersonProperties(props)`, `register(props)` (sent with every event), `reset()`, `optOut()` (also stops recording), `optIn()`, `hasOptedOut()`, `startSessionRecording()`, `stopSessionRecording()`, `isSessionRecording()`, `getDistinctId()`, `getSessionId()`, `on(eventName, callback)`, `flush()`, `debug.show()`, `debug.hide()`.

Give your AI coding assistant this instruction to add it to a project:

> Add `<script src="https://YOUR_HOTSPOT_HOST/hotspot.js" data-api-key="YOUR_API_KEY" defer></script>` to the `<head>` of the root layout (Next.js `app/layout.tsx`, Vite `index.html`, SvelteKit `app.html`, Astro layout). Do not add other analytics tags. Call `window.hotspot.identify(userId, { email, name })` after sign-in. Put `data-hotspot-mask` on elements that show personal data you do not want in session replays.

## AI Agents, MCP Suite & Automated Service Login

Hotspot ships a built-in Model Context Protocol (MCP) server, a downloadable agent skill file, and a browser-based Connect Flow so coding agents, CLIs, and automated services can log in or register sites cleanly and store Secret Keys safely in local files.

### Downloadable Skill (`SKILL.md`)

- Hosted dynamically for each server at `/skill.md` (`/skill.md?download=1` triggers file download), `/llms.txt`, and MCP resource `hotspot://skill`.
- Checked into [`products/hotspot/skill/SKILL.md`](skill/SKILL.md).
- Install directly into an agent workspace:

```bash
mkdir -p .claude/skills/hotspot .agents/skills/hotspot
curl -fsSL https://YOUR_HOTSPOT_HOST/skill.md -o .claude/skills/hotspot/SKILL.md
cp .claude/skills/hotspot/SKILL.md .agents/skills/hotspot/SKILL.md
```

### Connecting via MCP

- **Streamable HTTP**: `https://YOUR_HOTSPOT_HOST/mcp`
- **Legacy SSE**: `https://YOUR_HOTSPOT_HOST/mcp/sse`
- **Stdio**: `python -m products.hotspot.mcp`
- Pass the site's private Secret Key (`hs_sec_...`) in `Authorization: Bearer hs_sec_...` (or `X-Hotspot-Secret-Key`). Never embed `hs_sec_...` in frontend JavaScript/HTML or git commits; keep it in gitignored `.env.hotspot.local` or `~/.config/hotspot/credentials.json` (`chmod 600`).

### Automated Browser Login / Device Connect Flow

1. Start a session: `POST /hotspot/api/connect/start/` with `{"client_name": "Your Tool", "domain": "optional.example.com"}` (optionally pass a loopback `"redirect_uri": "http://127.0.0.1:<port>/callback"`). Returns `device_code`, `user_code`, and `verification_uri` (`/connect/?code=XXXX-XXXX`).
2. User opens `/connect/` in their browser to approve an existing site, sign in with Domain + Secret Key, or register a new site (`key_mode="same"` or `"new"`).
3. Poll `POST /hotspot/api/connect/token/` with `{"device_code": "..."}`. Returns single-use credentials (`secret_key`, `api_key`, `domains`, `mcp_config`, `env_lines`, `credentials_json`).
4. Or run the bundled CLI helper:

```bash
python -m products.hotspot.mcp connect --url https://YOUR_HOTSPOT_HOST --save-env .env.hotspot.local --save-mcp .mcp.json
```

## HTTP API

Pages: `/` landing, `/demo/` sample site, `/dashboard/` (redirects to `/dashboard/login/` when a password is set), `/connect/` browser connect authorization page, `/skill.md` downloadable AI skill, `/hotspot.js`, `/llms.txt`. Assets: `/hotspot/assets/recorder.js`, `/hotspot/assets/replay.js`, `/hotspot/assets/replay.css`, `/hotspot/assets/world.json` (public, cached for a day).

The dashboard API accepts the sign-in cookie or `Authorization: Bearer <HOTSPOT_DASHBOARD_PASSWORD>`.

### `POST /hotspot/batch/`

Public, CORS `*`. Body (JSON, sent as `text/plain` to avoid a preflight):

```json
{"api_key": "hs_my_site", "sent_at": "...", "batch": [{"event": "$pageview", "timestamp": "2026-11-05T10:00:00Z", "properties": {"distinct_id": "...", "$session_id": "...", "$pathname": "/"}}]}
```

Answers `{"status": 1, "received": n}`, `401` for a missing or unknown key, `413` for an oversized body or batch.

### `POST /hotspot/replay/`

Public, CORS `*`. Body (JSON as `text/plain`; with `?compression=gzip` the body is gzip-compressed):

```json
{"api_key": "hs_my_site", "session_id": "...", "distinct_id": "...", "events": [{"type": 4, "timestamp": 1762336800000, "data": {"href": "https://...", "width": 1440, "height": 900}}, {"type": 2, "timestamp": 1762336800010, "data": {"node": {}}}]}
```

`events` are rrweb events (`type` 0 to 6, `timestamp` in milliseconds); items that are not are skipped. Answers `{"status": 1, "received": n, "stored": true}`; `stored` is `false` once the session has used its recording budget. `401` for a missing or unknown key, `403` when `HOTSPOT_REPLAY_ENABLED=0`, `413` for a body over the limit (measured after decompression) or more than 5000 events, `400` when every timestamp is outside the ingestion window.

### `GET /hotspot/api/overview/`

Query parameters: `hours` (0 = all), `status` (`all|converted|rage|error|bounced|engaged|active|identified|dead`), `device_type`, `browser`, `os`, `search`, `limit`, `funnel` (steps separated by `|`, each `path:/pricing`, `event:$form_submit`, `click:#selector`, or `text:Buy now`; a trailing `*` on a path or selector is a prefix match), `conversion_event`, `tz_offset` (the viewer's offset from UTC in minutes, so `trend` buckets start on their hours and days).

`hours`, `device_type`, `browser` and `os` define the scope every section is computed over; `status` and `search` only filter the `sessions` list. `breakdowns` is the exception: it is computed on the time range alone, so filter menus keep every device, browser and OS that exists. `scope.sessions_stored` counts every session the server holds and `scope.sessions_in_range` the ones inside the time range, so an empty result can say whether data exists at all.

```json
{
  "status": 1, "generated_at": 1762336800.0,
  "scope": {"hours": 24, "device_type": "all", "browser": "all", "os": "all", "status": "all", "search": "", "sessions_analyzed": 42, "sessions_in_range": 42, "sessions_stored": 300},
  "setup": {"version": "3.1.0", "site_url": "...", "sdk_url": ".../hotspot.js", "api_key": "hs_my_site", "ingestion_keys_configured": true, "dashboard_protected": true, "demo_enabled": true, "storage": "redis", "conversion_events": ["$form_submit"], "session_ttl_seconds": 604800, "replay_enabled": true, "max_recording_bytes": 10485760},
  "metrics": {"sessions": 42, "visitors": 31, "sessions_with_breakage": 9, "sessions_with_rage": 3, "sessions_with_dead_clicks": 7, "sessions_with_errors": 2, "filtered_sessions": 42, "active_sessions": 3, "identified_sessions": 5, "pageviews": 120, "clicks": 300, "rage_clicks": 4, "dead_clicks": 11, "form_submits": 6, "errors": 2, "converted_sessions": 6, "conversion_rate": 14.3, "bounce_rate": 31.0, "avg_duration_seconds": 84.2, "avg_scroll_depth": 61, "ai_referral_sessions": 1, "sessions_with_replay": 38},
  "sessions": [{"session_id": "...", "distinct_id": "...", "label": "Visitor a1b2c3", "identity": {"email": "..."}, "status": "rage", "first_seen": 0, "last_seen": 0, "seconds_ago": 12, "duration_seconds": 40, "active_seconds": 30, "event_count": 9, "pageview_count": 2, "click_count": 4, "rage_click_count": 1, "dead_click_count": 1, "form_submit_count": 0, "error_count": 0, "custom_event_count": 0, "pages": ["/", "/pricing"], "entry_page": "/", "exit_page": "/pricing", "entry_url": "https://...", "entry_title": "...", "referrer": "...", "referrer_host": "google.com", "is_ai_referral": false, "ai_source": null, "utm": {}, "browser": "Chrome", "browser_version": "130.0.0.0", "os": "macOS", "device_type": "desktop", "country": "DE", "viewport_width": 1440, "viewport_height": 900, "language": "en-US", "timezone": "Europe/Berlin", "max_scroll_depth": 72, "converted": false, "conversion_event": null, "last_event": "$pageleave", "has_recording": true, "recording_events": 812, "recording_started_at": 1762336800.0, "recording_truncated": false}],
  "funnel": {"steps": [{"index": 1, "spec": "event:$pageview", "kind": "event", "value": "$pageview", "label": "Viewed a page", "count": 42, "pct_of_first": 100.0, "conv_from_prev": 100.0, "dropoff_from_prev": 0.0, "dropoff_count": 0, "median_seconds_to_next": 12.0}], "sessions_analyzed": 42, "conversion_rate": 14.3},
  "paths": {"flow": {"steps": [[{"pathname": "/", "count": 30, "exits": 8, "converted": 4, "other": false}]], "links": [{"step": 0, "from": 0, "to": 0, "count": 12}], "sessions": 42}, "entry_pages": [{"pathname": "/", "count": 30, "share": 71.4}], "exit_pages": [], "transitions": [{"from": "/", "to": "/pricing", "count": 12, "share": 40.0}], "top_paths": [{"pages": ["/", "/pricing"], "count": 12, "share": 28.6, "converted": 2}]},
  "friction": {"rage_clicks": [{"pathname": "/pricing", "selector": "#plan-enterprise", "text": "Enterprise", "count": 4, "sessions": 3, "last_seen": 0}], "dead_clicks": []},
  "pages": [{"pathname": "/", "views": 50, "sessions": 40, "avg_seconds": 31.2, "avg_scroll_depth": 58, "exits": 12, "exit_rate": 30.0, "converted": 6, "conversion_rate": 15.0, "rage_clicks": 1, "dead_clicks": 3, "errors": 0, "vitals": {"lcp_ms": 1800, "cls": 0.04, "inp_ms": 120, "fcp_ms": 900, "ttfb_ms": 210, "samples": 31}}],
  "web_vitals": {"lcp_ms": 2100, "cls": 0.05, "inp_ms": 140, "fcp_ms": 950, "ttfb_ms": 230, "samples": 90},
  "referrers": {"channels": [{"channel": "search", "sessions": 12, "share": 28.6, "converted": 2, "conversion_rate": 16.7}], "sources": [{"host": "direct", "sessions": 20, "share": 47.6, "converted": 3, "conversion_rate": 15.0}], "ai_sessions": 1, "ai_sources": [{"source": "ChatGPT", "sessions": 1}]},
  "errors": [{"message": "x is not a function", "type": "TypeError", "count": 2, "sessions": 2, "last_seen": 0, "pathname": "/checkout"}],
  "activity": {"events_per_minute": [0, 0, 3], "sessions_per_hour": [0, 2, 1]},
  "trend": {"bucket_seconds": 3600, "start": 1762250400.0, "sessions": [2, 0, 5], "visitors": [2, 0, 4], "pageviews": [6, 0, 12], "converted": [0, 0, 1]},
  "comparison": {"sessions": 38, "visitors": 29, "pageviews": 101, "converted_sessions": 4, "conversion_rate": 10.5, "bounce_rate": 44.7, "avg_duration_seconds": 61.0},
  "realtime": {"window_seconds": 1800, "active_now": 3, "active_in_window": 7, "countries": [{"country": "DE", "sessions": 4}], "pages": [{"pathname": "/pricing", "sessions": 2}], "sessions_per_minute": [0, 1, 2]},
  "breakdowns": {"device_type": {"desktop": 30, "mobile": 12}, "browser": {"Chrome": 25}, "os": {"macOS": 20}, "country": {"DE": 10}}
}
```

Web vitals are the 75th percentile of the samples in scope, the way Google scores them; `vitals` is `null` for a page without samples. Null means unknown. Nothing is invented when data is missing.

### `GET /hotspot/api/sessions/<session_id>/`

`{"status": 1, "session": {...same shape as a sessions row...}, "events": [{"event": "$autocapture", "timestamp": 1762336800.5, "properties": {...}}]}` with events in time order. `404` when the session is unknown or expired.

### `GET /hotspot/api/sessions/<session_id>/recording/?from=0`

`{"status": 1, "session_id": "...", "from": 0, "next": 3, "truncated": false, "started_at": 1762336800.0, "events": [...rrweb events in order...]}`. The response can be several megabytes. `from` is a chunk cursor: pass the previous answer's `next` to receive only chunks stored since, which is how the dashboard follows a session that is still active. An existing session without a recording answers with an empty `events` list; `404` when the session is unknown or expired.

rrweb custom events (`type` 5) carry the console and network capture: `data.tag` is `console` with `payload: {level, message, t}` or `network` with `payload: {kind, method, url, status, ok, duration_ms, t}`, where `t` is the time of the entry in milliseconds.

### `GET /hotspot/api/events/?since=<unix seconds>&limit=100`

`{"status": 1, "now": 0, "events": [{"event": "...", "timestamp": 0, "session_id": "...", "distinct_id": "...", "properties": {...}}]}`, newest first.

## Templates

The pages are single files under `templates/hotspot/`. Both servers render them: Django with its template engine and the standalone server with `templating.render_string`, so they use only `{{ name }}` placeholders and nothing else from Django's template language. Available placeholders: `page_title`, `site_url`, `sdk_url`, `api_key`, `csp_nonce`, `version`, `dashboard_protected` (`"1"` or empty), `demo_enabled`, `replay_enabled`, `storage` (`redis` or `memory`), and `error` on the login page.

Every page is served with a content security policy: inline scripts must carry `nonce="{{ csp_nonce }}"`, inline event handler attributes do not run, scripts load only from the same origin, styles from the same origin or Google Fonts, and `connect-src` is the same origin. The dashboard additionally allows images, stylesheets, fonts and media from any origin, because the replay frame rebuilds recorded pages and inherits the policy; it still runs no scripts.

## Privacy and retention

Hotspot stores event properties, a random visitor id, a session id, whatever you pass to `identify()`, and the DOM recording of each session. It does not store IP addresses, request bodies or headers, or the values of masked fields; analytics events never carry input values at all. The country shown on the realtime map comes from a CDN country header when there is one, otherwise from the timezone the browser reports, so it is never more precise than a country. Recordings expire with their session after `HOTSPOT_SESSION_TTL_SECONDS`. Visitors can opt out with `hotspot.optOut()`, which also stops the recording.

## Tests

```bash
uv run pytest products/hotspot/backend/test
```
