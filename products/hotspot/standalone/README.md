# Hotspot standalone server

Runs Hotspot on its own: a Starlette app, Uvicorn, and Redis for storage. Nothing from the PostHog stack is needed. The handlers in `server.py` are thin wrappers around `products/hotspot/backend/service.py`, the same code the Django app uses, so both deployments behave the same.

## Run locally

```bash
pip install -r products/hotspot/standalone/requirements.txt
HOTSPOT_DASHBOARD_PASSWORD=change-me HOTSPOT_API_KEYS=hs_my_site python products/hotspot/standalone/server.py
```

Without `REDIS_URL` the server keeps sessions in memory, so they disappear on restart. From the repository root, `./bin/hotspot standalone` runs the same command with the values from `.env.hotspot`.

## Run with Docker

```bash
cd products/hotspot/standalone
HOTSPOT_DASHBOARD_PASSWORD=change-me HOTSPOT_API_KEYS=hs_my_site docker compose up -d --build
```

This starts the server on port 8000 and a Redis container with persistence. Set `HOTSPOT_SITE_URL` to the public origin (for example `https://hotspot.example.com`) so install snippets point at it. Put a TLS-terminating proxy in front for production.

All settings are listed in [the product README](../README.md#configuration).
