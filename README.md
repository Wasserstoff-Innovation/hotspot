<p align="center">
  <svg width="64" height="64" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
    <circle cx="16" cy="16" r="14" stroke="#FF5722" stroke-width="2" stroke-dasharray="3 3" opacity="0.45" />
    <circle cx="16" cy="16" r="9.5" stroke="#FF5722" stroke-width="2.5" opacity="0.85" />
    <circle cx="16" cy="16" r="5.2" fill="#FF5722" />
    <circle cx="14.8" cy="14.8" r="1.6" fill="#FFF" opacity="0.9" />
  </svg>
</p>

<h1 align="center">Hotspot</h1>

<p align="center">
  <strong>Unified Product Analytics, Click Heatmaps & User Journey Intelligence</strong>
</p>

<p align="center">
  <em>Self-hosted, 100% private, zero telemetry, and loaded via a single lightweight script.</em>
</p>

---

## ⚡ The 1-Line Drop-in Integration

Track pageviews, user interactions, click coordinates for visual heatmaps, form submissions, and errors by adding **one single line** to your website or web app:

```html
<script src="http://localhost:8000/hotspot.js" data-api-key="YOUR_PROJECT_API_KEY"></script>
```

### What It Does Automatically
- 🚀 **Autocapture**: Clicks, button interactions, links, and form submissions without manual tagging.
- 🗺️ **Visual Click Heatmaps**: Records precise click coordinates (`$heatmap_x`, `$heatmap_y`, `$x_ratio`, `$y_ratio`) for rendering radiant heat zones and rage clicks.
- ⏱️ **Pageview & Engagement**: Automatically calculates active duration and scroll depth percentage on page exit or tab change.
- 🔄 **Single Page App (SPA) Support**: Automatically tracks route changes with `pushState`, `replaceState`, and `popstate`.
- 🛡️ **Zero Telemetry**: All events stay entirely within your self-hosted infrastructure.

---

## 💻 Developer JavaScript API

The tracker attaches seamlessly to `window.hotspot`:

```javascript
// Send custom events
window.hotspot.capture('purchase_completed', {
    plan: 'enterprise',
    price: 499.00,
    currency: 'USD'
});

// Identify user
window.hotspot.identify('user_12345', {
    email: 'alex@example.com',
    company: 'Acme Corp',
    role: 'Product Lead'
});

// Set global super properties sent with every event
window.hotspot.register({
    app_version: '2.4.0',
    environment: 'production'
});

// Reset user identity on logout
window.hotspot.reset();
```

---

## 🚀 Quickstart: Running Hotspot Locally

Hotspot comes with an all-in-one management CLI:

```bash
# 1. Start the complete standalone stack (Postgres, ClickHouse, Redis, Kafka, Web)
./bin/hotspot start

# 2. Check service status
./bin/hotspot status

# 3. View live container logs
./bin/hotspot logs web

# 4. Stop the stack
./bin/hotspot stop
```

Once started, open your browser:
- **Hotspot Landing Page & Playground**: [http://localhost:8000](http://localhost:8000)
- **Hotspot Web Dashboard**: [http://localhost:8000/login](http://localhost:8000/login)
- **Single-Script Tracker SDK**: [http://localhost:8000/hotspot.js](http://localhost:8000/hotspot.js)

---

## 🛠️ Architecture

- **Web & API Backend**: Django 5 + ASGI server (`granian`)
- **Event Ingestion**: Direct HTTP ingest (`/capture/`, `/batch/`, `/e/`) with cross-origin resource sharing (CORS)
- **Database Layer**: PostgreSQL 15 (relational models) & ClickHouse (high-speed event analytics)
- **Queue & Storage**: Kafka (Redpanda) & SeaweedFS S3-compatible object storage
- **Client SDK**: Zero-dependency `hotspot.js` (<12KB)

---

## 📄 License

Apache 2.0.
