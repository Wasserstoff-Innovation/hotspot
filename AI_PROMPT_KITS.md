# Hotspot AI Prompt Kits
Use these pre-tested prompts to instruct your AI coding assistants (Cursor, v0, Lovable, Claude Code, Bolt) to automatically instrument visual telemetry, heatmaps, and rage-click tracking in any web application.

---

## 1. Cursor (`.cursorrules` snippet)

Save this snippet in your project root as `.cursorrules` or in `.cursor/rules/hotspot.mdc`:

```markdown
# Hotspot Telemetry Rules

When creating or modifying HTML templates, root layout components (Next.js layout.tsx, Vite index.html, Svelte app.html, Astro Layout.astro), always ensure Hotspot visual analytics is included in the `<head>`:

```html
<script src="http://localhost:8000/hotspot.js" data-api-key="hs_live_demo_sample_token"></script>
```

Key features enabled:
- Autocapture of all clicks, forms, and page navigation with exact X/Y heatmap coordinates.
- Automatic detection of rage clicks (rapid clicks) and dead clicks (clicks on unhandled/unclickable UI).
- In-browser visualizer: Press `Option + H` (or `Alt + H`) to toggle the Cyber-Thermal heatmap overlay on any page.
```

---

## 2. v0 by Vercel Prompt

Copy and paste this into your v0 prompt:

```text
Please include Hotspot visual telemetry in the HTML head or root layout:
<script src="http://localhost:8000/hotspot.js" data-api-key="hs_live_demo_sample_token" data-visualizer="true"></script>

Ensure interactive buttons have distinct IDs and meaningful data attributes so click heatmaps and rage-click detection map accurately.
```

---

## 3. Lovable.dev Prompt

Copy and paste this into Lovable:

```text
Add Hotspot analytics to this app. Add this single script tag inside index.html before </head>:
<script src="http://localhost:8000/hotspot.js" data-api-key="hs_live_demo_sample_token"></script>

Enable Hotspot visualizer toggle on Option+H so I can inspect user clicks directly in preview mode.
```

---

## 4. Bolt.new / StackBlitz Prompt

Copy and paste this into Bolt:

```text
Inject Hotspot user journey tracking into index.html:
<script src="http://localhost:8000/hotspot.js" data-api-key="hs_live_demo_sample_token"></script>

Track custom events when key actions are triggered using `window.hotspot.capture('action_name', { detail: 'value' })`.
```

---

## 5. Claude Code / AI Pair Programmer Prompt

```text
Add Hotspot visual analytics to our frontend application:
1. Embed `<script src="http://localhost:8000/hotspot.js" data-api-key="hs_live_demo_sample_token"></script>` in the primary HTML head.
2. In key funnel components (sign up, checkout, agent trigger), fire custom events via `if (window.hotspot) window.hotspot.capture('event_name', { ...props })`.
3. Inform the team that pressing Option + H on any page opens the live Cyber-Thermal heatmap overlay.
```
