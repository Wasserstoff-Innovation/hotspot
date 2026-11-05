<p align="center">
  <svg width="64" height="64" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
    <circle cx="16" cy="16" r="14" stroke="#2563EB" stroke-width="2" stroke-dasharray="3 3" opacity="0.45" />
    <circle cx="16" cy="16" r="9.5" stroke="#2563EB" stroke-width="2.5" opacity="0.85" />
    <circle cx="16" cy="16" r="5.2" fill="#10B981" />
  </svg>
</p>

<h1 align="center">Hotspot</h1>

<p align="center"><strong>Self-hosted product telemetry from one script tag.</strong></p>

Hotspot records what visitors do on your site and shows you where they get stuck.
One script captures page views, clicks, rage clicks, dead clicks, scroll depth, form submits, JavaScript errors and single-page-app route changes.
Your own server stores the events, and the dashboard shows sessions, a replay timeline, funnels you define, paths, friction by element, pages, referrers and errors.
No data leaves your infrastructure.

The product lives in [`products/hotspot/`](products/hotspot/README.md).
The rest of this repository is the PostHog codebase it is built on; `posthog/urls.py` includes Hotspot's routes so the Django web process serves it.

## Install the script

```html
<script src="https://YOUR_HOTSPOT_HOST/hotspot.js" data-api-key="YOUR_API_KEY" defer></script>
```

```javascript
window.hotspot.capture('plan_selected', { plan: 'team' })
window.hotspot.identify('user_123', { email: 'user@example.com', name: 'Sam' })
window.hotspot.register({ app_version: '3.0.0' })
window.hotspot.optOut()
```

The script has no dependencies and is under 10 KB gzipped.
It stores a visitor id and a session id in `localStorage`, sets no third-party cookies, and never records form input values.
All events and options are documented in [the product README](products/hotspot/README.md).

## Run it

Standalone, with nothing but Python and Redis:

```bash
pip install -r products/hotspot/standalone/requirements.txt
HOTSPOT_DASHBOARD_PASSWORD=change-me HOTSPOT_API_KEYS=hs_my_site python products/hotspot/standalone/server.py
```

Or as a container with persistent Redis: `cd products/hotspot/standalone && docker compose up -d --build`.

Inside the full stack from this repository:

```bash
./bin/hotspot start      # Postgres, ClickHouse, Redis, Kafka, object storage and the web process
./bin/hotspot status
./bin/hotspot logs web
./bin/hotspot stop
```

Then open:

- Landing page: http://localhost:8000
- Dashboard: http://localhost:8000/dashboard/ (set `HOTSPOT_DASHBOARD_PASSWORD` in `.env.hotspot` to protect it)
- Sample site for generating events: http://localhost:8000/demo/
- Script: http://localhost:8000/hotspot.js

## How it is built

- `products/hotspot/sdk/hotspot.js`: the script visitors load.
- `products/hotspot/backend/`: ingestion with size limits and api key checks, session summaries in Redis (memory fallback), the analytics that feed the dashboard, and password sign-in for the dashboard. The domain code has no framework dependency.
- `products/hotspot/backend/views.py` and `products/hotspot/standalone/server.py`: thin Django and Starlette entrypoints over the same service layer.
- `products/hotspot/templates/hotspot/`: the landing page, dashboard, sample site and sign-in page, each a single file served with a content security policy.

Tests: `uv run pytest products/hotspot/backend/test`.

## License

Apache 2.0.
