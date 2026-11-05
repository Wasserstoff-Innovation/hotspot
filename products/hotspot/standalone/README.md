# Hotspot Standalone Architecture

This directory houses the standalone, zero-PostHog deployment architecture for Hotspot.

## Architecture

- **`products/hotspot/backend/`**: Pure domain logic, simulation engine, session ingestion, and dropoff analytics.
- **`products/hotspot/sdk/hotspot.js`**: Pure resident client tracking script (0 external dependencies, 0 PostHog references).
- **`products/hotspot/templates/`**: High-definition conversion intelligence dashboard & landing page.
- **`products/hotspot/standalone/server.py`**: A lightweight Starlette/Uvicorn server requiring only Python and ~60MB RAM (replaces the 2GB PostHog Docker image).

## Running Standalone (Without PostHog)

```bash
cd products/hotspot/standalone
pip install starlette uvicorn
python server.py
```

Or with Docker:

```bash
docker build -f Dockerfile -t hotspot-standalone ../../..
docker run -p 8000:8000 hotspot-standalone
```
