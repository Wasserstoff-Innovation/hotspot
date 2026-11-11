---
name: hotspot-analytics
description: Connect to Hotspot telemetry, authorize through the browser or register sites, store secret keys safely in local files, install the tracking snippet, verify sessions, and pull analytics/charts via MCP.
---

# Hotspot Analytics & Session Replay Skill

Connect an AI coding assistant or automated service to **{{ site_url }}** cleanly, keep private Secret Keys (`hs_sec_...`) out of git and frontend bundles, install the SDK tag, run test sessions, and pull chart-ready product analytics over MCP.

## Endpoints on This Deployment

- **Server origin**: `{{ site_url }}`
- **Download this skill**: `{{ site_url }}/skill.md` (`?download=1` to force file download)
- **Browser authorization page**: `{{ site_url }}/connect/`
- **MCP Streamable HTTP**: `{{ site_url }}/mcp`
- **MCP Legacy SSE**: `{{ site_url }}/mcp/sse`
- **Tracking script**: `{{ sdk_url }}`
- **LLM index**: `{{ site_url }}/llms.txt`

---

## 1. Security & Local Secret Key Storage Rules (Read First)

Hotspot issues two distinct keys per site:

| Key prefix | Purpose | Where it belongs |
| --- | --- | --- |
| `hs_live_...` | Public ingest key (`api_key`) | Frontend `<script data-api-key="...">` tag. Only allows sending telemetry batches & replay chunks. |
| `hs_sec_...` | Private admin key (`secret_key`) | MCP `Authorization: Bearer ...` header, `/dashboard/login/`, site creation (`confirmation_key`, `key_mode="same"`), and local env/credential files **only**. |

**Hard rules for every agent and automated service:**
1. **Never** put `hs_sec_...` inside frontend HTML, React/Next.js client bundles, `NEXT_PUBLIC_*` / `VITE_*` client env vars, or git commits.
2. **Before** writing any local credentials file (`.env.hotspot.local` or `.hotspot/credentials.json`), inspect `.gitignore` in the repo root and ensure both `.env.hotspot.local` and `.hotspot/` are ignored.
3. Restrict file permissions to owner-only (`chmod 600` on files, `chmod 700` on `.hotspot/` or `~/.config/hotspot/`).
4. **Never** overwrite an existing domain without the owner's `confirmation_key` or browser approval.

### Recommended Local Credential File Formats

**Option A — Per-project `.env.hotspot.local` (best alongside web apps):**
```bash
grep -qxF '.env.hotspot.local' .gitignore 2>/dev/null || printf '\n.env.hotspot.local\n.hotspot/\n' >> .gitignore
cat <<'EOF' > .env.hotspot.local
HOTSPOT_HOST={{ site_url }}
HOTSPOT_DOMAIN=app.example.com
HOTSPOT_API_KEY=hs_live_...
HOTSPOT_SECRET_KEY=hs_sec_...
EOF
chmod 600 .env.hotspot.local
```

**Option B — Shared user credential file (`~/.config/hotspot/credentials.json`):**
```bash
mkdir -p ~/.config/hotspot && chmod 700 ~/.config/hotspot
cat <<'EOF' > ~/.config/hotspot/credentials.json
{
  "site_url": "{{ site_url }}",
  "mcp_url": "{{ site_url }}/mcp",
  "domain": "app.example.com",
  "api_key": "hs_live_...",
  "secret_key": "hs_sec_..."
}
EOF
chmod 600 ~/.config/hotspot/credentials.json
```

---

## 2. How Automated Services & AI Agents Log In / Add a Site

### Path A — Browser-Verified Connect Flow (Recommended for existing accounts or human confirmation)
Use this when the user already has a Hotspot account/site, wants to approve from their browser session, or prefers entering credentials / creating a site in the browser instead of pasting secrets into chat:

1. **Start a connect session:**
   ```bash
   curl -sS -X POST "{{ site_url }}/hotspot/api/connect/start/" \
     -H "Content-Type: application/json" \
     -d '{"client_name":"AI Assistant","domain":"app.example.com"}'
   ```
   Response includes `device_code`, `user_code` (e.g. `ABCD-1234`), `verification_uri` (`{{ site_url }}/connect/?code=ABCD-1234`), `expires_in` (`600`), and `interval` (`2`).
   *(Optional for local CLIs with an HTTP callback listener: include `"redirect_uri": "http://127.0.0.1:<port>/callback"` and `"state": "<random>"` — Hotspot strictly allows loopback `127.0.0.1` / `localhost` URLs and appends `?code=<device_code>&state=...`.)*

2. **Ask the user to open `verification_uri`** in their browser. On `/connect/`, they can:
   - Approve an already signed-in dashboard site with one click, OR
   - Sign in with their Domain + Secret Key (`hs_sec_...`), OR
   - Register a brand new website right on the page (same or new key).

3. **Poll once every `interval` seconds for the single-use grant:**
   ```bash
   curl -sS -X POST "{{ site_url }}/hotspot/api/connect/token/" \
     -H "Content-Type: application/json" \
     -d '{"device_code":"<device_code>"}'
   ```
   - HTTP `202` (`"pending": true`) -> keep waiting.
   - HTTP `200` -> immediately returns `secret_key`, `api_key`, `domain`, `domains`, `mcp_url`, `mcp_config`, `env_lines`, and `credentials_json`, and burns the code so it cannot be replayed.

4. **Persist `env_lines` or `secret_key` locally (`chmod 600` + `.gitignore`)** and configure `.mcp.json` using the returned `mcp_config`.

5. **CLI one-shot shortcut** (inside repos containing `products.hotspot`):
   ```bash
   python -m products.hotspot.mcp connect --url {{ site_url }} --save-env .env.hotspot.local --save-mcp .mcp.json
   ```

### Path B — Direct Registration (For Brand-New Sites)
Call the unauthenticated MCP tool `register_site(name="My App", domain="app.example.com")` OR `POST {{ site_url }}/hotspot/api/sites/` (`{"name": "My App", "domain": "app.example.com", "key_mode": "new"}`).
- Save `secret_key` (`hs_sec_...`) immediately into `.env.hotspot.local` / `~/.config/hotspot/credentials.json` (`0600`) and `.mcp.json`.
- If `409 Conflict` is returned because the domain already exists, stop and either request `confirmation_key` from the user or use **Path A** (`/connect/`) so the domain owner authorizes cleanly in their browser.

### Path C — Adding Additional Websites (Same or New Secret Key)
When authenticated with `Authorization: Bearer hs_sec_...`:
- Pass `key_mode="same"` to `register_site` (or `POST /hotspot/api/sites/`) to share the same Secret Key across multiple sites (staging, docs, marketing, sub-apps) so one MCP config accesses all of them.
- Pass `key_mode="new"` to issue an isolated `hs_sec_...` for the new domain while keeping it visible in your dashboard session.

---

## 3. Configure MCP Clients

Ensure `.mcp.json` (or global IDE MCP config) stays gitignored if it contains a literal `hs_sec_...`, or reference `${HOTSPOT_SECRET_KEY}` from `.env.hotspot.local` where supported:

### `.mcp.json` (Claude Code / Cursor / Antigravity / Windsurf)
```json
{
  "mcpServers": {
    "hotspot": {
      "type": "http",
      "url": "{{ site_url }}/mcp",
      "headers": {
        "Authorization": "Bearer ${HOTSPOT_SECRET_KEY}"
      }
    }
  }
}
```

### Claude Code CLI
```bash
claude mcp add --transport http hotspot {{ site_url }}/mcp \
  --header "Authorization: Bearer $HOTSPOT_SECRET_KEY"
```

### Legacy SSE Clients
- URL: `{{ site_url }}/mcp/sse` with header `Authorization: Bearer <HOTSPOT_SECRET_KEY>`

---

## 4. Integrate the SDK, Verify, and Pull Data

1. **Always read instructions first** via MCP tool `get_important_instructions()` (or resource `hotspot://instructions/important`).
2. **Fetch the exact code snippet for the codebase** via `get_site_setup(framework="nextjs_app")` (`html`, `nextjs_app`, `nextjs_pages`, `react_vite`, `sveltekit`, `astro`, `django`).
3. **Add only the public `hs_live_...` snippet** to `<head>` / root layout:
   ```html
   <script src="{{ sdk_url }}" data-api-host="{{ site_url }}" data-api-key="hs_live_..." defer></script>
   ```
   Add `{{ site_url }}` to `script-src` and `connect-src` if the site uses CSP.
4. **Instrument key user actions** (`window.hotspot.identify(...)`, `.capture(...)`, `.reset()` on logout, `data-hotspot-mask` around PII). Remember the method is `.capture(...)`, not `.track(...)`.
5. **Test & verify**: run `send_test_session` -> `verify_connection` -> `list_sessions` -> `get_session_detail(session_id=..., include_recording_diagnostics=True)`.
6. **Pull charts & UX diagnostics**: call `get_chart_data`, `get_funnel_analysis` (includes auto-discovered `suggested_funnels`), `get_user_flows`, `get_breakage_and_errors`, `get_pages_and_vitals`, `get_referrers_and_ai_traffic`, `get_realtime_snapshot`, and `get_live_events`. Pass `site="domain.com"` or `site="all"` when sharing a secret key across multiple sites.
