"""Hotspot documentation catalog, SEO schemas, sitemap, and robots generators."""

from __future__ import annotations

import json
from typing import Any

DOC_CATEGORIES = [
    {
        "id": "getting-started",
        "title": "Getting Started",
        "icon": "rocket",
        "slugs": ["overview", "quickstart", "frameworks"],
    },
    {
        "id": "crm-intelligence",
        "title": "CRM & User Intelligence",
        "icon": "users",
        "slugs": ["crm-identity", "customer-journey", "vip-radar"],
    },
    {
        "id": "funnels-analytics",
        "title": "Funnels & Diagnostics",
        "icon": "funnel",
        "slugs": ["funnels", "breakage", "session-replay"],
    },
    {
        "id": "sdk-api",
        "title": "SDK & Privacy API",
        "icon": "code",
        "slugs": ["sdk-reference", "privacy-masking"],
    },
    {
        "id": "ai-ops",
        "title": "AI & Self-Hosting",
        "icon": "server",
        "slugs": ["mcp-ai", "self-hosting"],
    },
]

DOCS_PAGES: dict[str, dict[str, Any]] = {
    "overview": {
        "slug": "overview",
        "category": "getting-started",
        "title": "Overview & Architecture",
        "nav_label": "Overview",
        "badge": "Core",
        "description": "Hotspot is self-hosted product telemetry, session replay, and behavioral intelligence. See where users get stuck without sending data to third parties.",
        "keywords": [
            "self hosted session replay",
            "open source hotjar alternative",
            "microsoft clarity alternative self hosted",
            "privacy first product analytics",
            "zero pii session recording",
            "product telemetry",
        ],
        "prev_slug": None,
        "next_slug": "quickstart",
        "content_html": """
<div class="doc-lead">
    <strong>Hotspot</strong> is a lightweight, self-hosted product telemetry and behavioral analytics engine.
    With a single ~12KB script tag, it records full session replays, rage clicks, dead clicks, client errors, conversion funnels, and CRM customer journeys, keeping all data 100% on your own server.
</div>

<div class="callout tip">
    <div class="callout-icon">💡</div>
    <div class="callout-body">
        <strong>Privacy-First & Zero-PII:</strong> Unlike conventional analytics tools, Hotspot automatically redacts sensitive data, hashes user identifiers with tenant-scoped cryptographic salts, and does not require third-party cookie consent banners.
    </div>
</div>

<h2>Why Hotspot? Comparison with Other Platforms</h2>
<p>Modern engineering and product teams need deep behavioral visibility without vendor lock-in, data privacy violations, or sluggish page loads.</p>

<div class="doc-table-wrap">
    <table class="doc-table">
        <thead>
            <tr>
                <th>Capability</th>
                <th>Hotspot (Self-Hosted)</th>
                <th>Microsoft Clarity</th>
                <th>Hotjar / FullStory</th>
            </tr>
        </thead>
        <tbody>
            <tr>
                <td><strong>Data Hosting</strong></td>
                <td><span class="badge success">100% On-Prem / Cloud</span></td>
                <td><span class="badge danger">Microsoft Cloud</span></td>
                <td><span class="badge danger">Vendor Cloud</span></td>
            </tr>
            <tr>
                <td><strong>Script Size</strong></td>
                <td><span class="badge success">~12 KB gzipped</span></td>
                <td>~45 KB</td>
                <td>~65+ KB</td>
            </tr>
            <tr>
                <td><strong>Zero-PII Hashing</strong></td>
                <td><span class="badge success">Deterministic HMAC-SHA256</span></td>
                <td><span class="badge neutral">Basic Masking</span></td>
                <td><span class="badge neutral">Manual Redaction</span></td>
            </tr>
            <tr>
                <td><strong>Drop-Off Diagnostics</strong></td>
                <td><span class="badge success">Automated Root-Cause</span></td>
                <td>Manual Inspection</td>
                <td>Manual Inspection</td>
            </tr>
            <tr>
                <td><strong>VIP Customer Radar</strong></td>
                <td><span class="badge success">Real-Time Friction Alerts</span></td>
                <td><span class="badge neutral">No</span></td>
                <td><span class="badge neutral">Enterprise Addon</span></td>
            </tr>
            <tr>
                <td><strong>AI / MCP Integration</strong></td>
                <td><span class="badge success">Native MCP Server</span></td>
                <td><span class="badge neutral">No</span></td>
                <td><span class="badge neutral">No</span></td>
            </tr>
        </tbody>
    </table>
</div>

<h2>System Architecture</h2>
<p>Hotspot is designed with a lightweight, decoupled architecture that scales effortlessly on modest hardware:</p>

<div class="doc-steps">
    <div class="doc-step">
        <div class="doc-step-num">1</div>
        <div class="doc-step-body">
            <h3>Edge Telemetry SDK (<code>hotspot.js</code>)</h3>
            <p>Runs in browser background. Captures DOM mutations via <code>rrweb</code>, pageviews, clicks, dead clicks, rage clicks, JavaScript exceptions, and web vitals without impacting main thread performance.</p>
        </div>
    </div>
    <div class="doc-step">
        <div class="doc-step-num">2</div>
        <div class="doc-step-body">
            <h3>Ingestion & Mutator Pipeline</h3>
            <p>Receives batched beacon payloads at <code>/hotspot/batch/</code> and recording chunks at <code>/hotspot/replay/</code>. Computes live session state, user hashes, and funnel step progression on the fly.</p>
        </div>
    </div>
    <div class="doc-step">
        <div class="doc-step-num">3</div>
        <div class="doc-step-body">
            <h3>In-Memory & Redis Persistence</h3>
            <p>Maintains sessions, site definitions, and events in Redis with automatic TTL pruning, multi-tenant site partitioning, and high-throughput write performance.</p>
        </div>
    </div>
    <div class="doc-step">
        <div class="doc-step-num">4</div>
        <div class="doc-step-body">
            <h3>Dashboard, Analytics & MCP API</h3>
            <p>Zero-build standalone dashboard, customer journey drawer, funnel diagnostics, and streaming Model Context Protocol (MCP) server for AI assistants.</p>
        </div>
    </div>
</div>
""",
    },
    "quickstart": {
        "slug": "quickstart",
        "category": "getting-started",
        "title": "Quickstart & 1-Line Embed",
        "nav_label": "Quickstart",
        "badge": "Popular",
        "description": "Get Hotspot running in 60 seconds. Paste the 1-line script tag into your website's head and verify real-time telemetry.",
        "keywords": [
            "hotspot quickstart",
            "hotspot embed script",
            "install session replay",
            "website telemetry script",
        ],
        "prev_slug": "overview",
        "next_slug": "frameworks",
        "content_html": """
<div class="doc-lead">
    Integrating Hotspot into any website or web application takes less than a minute. Paste the script tag into your HTML <code>&lt;head&gt;</code> and start watching live sessions.
</div>

<h2>1. Copy the Script Tag</h2>
<p>Add this snippet to your HTML before the closing <code>&lt;/head&gt;</code> tag. Replace <code>YOUR_API_KEY</code> with your site's public tracking key (e.g., from the Hotspot dashboard Sites & Setup tab):</p>

<div class="code-block-wrap">
    <div class="code-block-header">
        <span class="code-lang">HTML</span>
        <button class="doc-copy-btn" type="button" data-copy-target="code-snippet-embed">Copy Snippet</button>
    </div>
    <pre><code id="code-snippet-embed">&lt;script
  src="https://hotspot.buxa.ai/hotspot.js"
  data-api-host="https://hotspot.buxa.ai"
  data-api-key="hs_live_YOUR_SITE_KEY"
  defer&gt;&lt;/script&gt;</code></pre>
</div>

<h2>Configuration Attributes</h2>
<div class="doc-table-wrap">
    <table class="doc-table">
        <thead>
            <tr>
                <th>Attribute</th>
                <th>Required</th>
                <th>Description</th>
            </tr>
        </thead>
        <tbody>
            <tr>
                <td><code>src</code></td>
                <td><span class="badge success">Yes</span></td>
                <td>URL to <code>hotspot.js</code> served by your Hotspot server.</td>
            </tr>
            <tr>
                <td><code>data-api-host</code></td>
                <td><span class="badge success">Yes</span></td>
                <td>Base URL of your Hotspot instance where telemetry payloads are posted.</td>
            </tr>
            <tr>
                <td><code>data-api-key</code></td>
                <td><span class="badge success">Yes</span></td>
                <td>Site tracking key (e.g. <code>hs_live_app_...</code>). Isolates data per tracked website.</td>
            </tr>
            <tr>
                <td><code>defer</code></td>
                <td>Recommended</td>
                <td>Ensures non-blocking asynchronous script loading so your initial page load is unaffected.</td>
            </tr>
        </tbody>
    </table>
</div>

<h2>2. Verify Live Connection</h2>
<p>Once deployed, open your website in a browser tab and test the connection:</p>
<ol class="doc-ordered-list">
    <li>Open your browser DevTools (<code>F12</code> or <code>Cmd+Option+I</code>) and switch to the <strong>Network</strong> tab.</li>
    <li>Filter by <code>hotspot</code>. You will see <code>hotspot.js</code> return HTTP 200, followed by periodic <code>/hotspot/batch/</code> beacons returning HTTP 202.</li>
    <li>Open your Hotspot dashboard and click the <strong>Realtime</strong> tab in the sidebar. You will see your active visit appear immediately on the global map and live event feed.</li>
</ol>
""",
    },
    "frameworks": {
        "slug": "frameworks",
        "category": "getting-started",
        "title": "Framework Integration Guides",
        "nav_label": "Framework Guides",
        "badge": "Guides",
        "description": "Step-by-step guides for installing Hotspot in Next.js (App & Pages Router), React, Vue, Nuxt, SvelteKit, Shopify, Webflow, and WordPress.",
        "keywords": [
            "hotspot nextjs",
            "hotspot react session replay",
            "hotspot vue nuxt integration",
            "hotspot shopify",
            "hotspot webflow analytics",
        ],
        "prev_slug": "quickstart",
        "next_slug": "crm-identity",
        "content_html": """
<div class="doc-lead">
    Production-ready integration recipes for all modern web frameworks and platforms.
</div>

<h2>Next.js (App Router)</h2>
<p>In Next.js 13/14/15 using the App Router, add the <code>&lt;Script&gt;</code> component inside your root <code>app/layout.tsx</code>:</p>

<div class="code-block-wrap">
    <div class="code-block-header">
        <span class="code-lang">TypeScript / TSX (app/layout.tsx)</span>
        <button class="doc-copy-btn" type="button" data-copy-target="code-nextjs-app">Copy</button>
    </div>
    <pre><code id="code-nextjs-app">import Script from 'next/script'

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    &lt;html lang="en"&gt;
      &lt;head&gt;
        &lt;Script
          src="https://hotspot.buxa.ai/hotspot.js"
          data-api-host="https://hotspot.buxa.ai"
          data-api-key="hs_live_YOUR_SITE_KEY"
          strategy="afterInteractive"
        /&gt;
      &lt;/head&gt;
      &lt;body&gt;{children}&lt;/body&gt;
    &lt;/html&gt;
  )
}</code></pre>
</div>

<h2>React SPA (Vite / CRA)</h2>
<p>Add the script tag to <code>index.html</code> right inside the <code>&lt;head&gt;</code>:</p>

<div class="code-block-wrap">
    <div class="code-block-header">
        <span class="code-lang">HTML (index.html)</span>
        <button class="doc-copy-btn" type="button" data-copy-target="code-react-vite">Copy</button>
    </div>
    <pre><code id="code-react-vite">&lt;!doctype html&gt;
&lt;html lang="en"&gt;
  &lt;head&gt;
    &lt;meta charset="UTF-8" /&gt;
    &lt;title&gt;My Application&lt;/title&gt;
    &lt;script
      src="https://hotspot.buxa.ai/hotspot.js"
      data-api-host="https://hotspot.buxa.ai"
      data-api-key="hs_live_YOUR_SITE_KEY"
      defer&gt;&lt;/script&gt;
  &lt;/head&gt;
  &lt;body&gt;
    &lt;div id="root"&gt;&lt;/div&gt;
    &lt;script type="module" src="/src/main.tsx"&gt;&lt;/script&gt;
  &lt;/body&gt;
&lt;/html&gt;</code></pre>
</div>

<h2>Vue 3 / Nuxt 3</h2>
<p>Configure Hotspot in <code>nuxt.config.ts</code>:</p>

<div class="code-block-wrap">
    <div class="code-block-header">
        <span class="code-lang">TypeScript (nuxt.config.ts)</span>
        <button class="doc-copy-btn" type="button" data-copy-target="code-nuxt3">Copy</button>
    </div>
    <pre><code id="code-nuxt3">export default defineNuxtConfig({
  app: {
    head: {
      script: [
        {
          src: 'https://hotspot.buxa.ai/hotspot.js',
          'data-api-host': 'https://hotspot.buxa.ai',
          'data-api-key': 'hs_live_YOUR_SITE_KEY',
          defer: true
        }
      ]
    }
  }
})</code></pre>
</div>

<h2>Shopify Stores</h2>
<p>In your Shopify Admin, go to <strong>Online Store &gt; Themes &gt; Edit Code</strong>. Open <code>layout/theme.liquid</code> and paste the script directly above <code>&lt;/head&gt;</code>.</p>

<h2>Webflow Sites</h2>
<p>In your Webflow Project Settings, go to <strong>Custom Code &gt; Head Code</strong>, paste the script tag, and publish your project to production.</p>
""",
    },
    "crm-identity": {
        "slug": "crm-identity",
        "category": "crm-intelligence",
        "title": "Zero-PII CRM Identity & Behavioral Radar",
        "nav_label": "CRM & Zero-PII",
        "badge": "New",
        "description": "Track authenticated user journeys, lifetime retention, and VIP customer friction without storing sensitive personal data.",
        "keywords": [
            "zero pii crm",
            "privacy first user identification",
            "deterministic user hash",
            "crm session replay",
            "vip customer monitoring",
        ],
        "prev_slug": "frameworks",
        "next_slug": "customer-journey",
        "content_html": """
<div class="doc-lead">
    Traditional analytics platforms store raw emails, names, and user records unencrypted, introducing GDPR, CCPA, and compliance liabilities. Hotspot solves this with <strong>Deterministic Cryptographic User Hashing</strong>.
</div>

<h2>How Zero-PII User Hashing Works</h2>
<p>When you call <code>hotspot.identify(distinctId, traits, { hash: true })</code>, the backend computes a tenant-salted HMAC-SHA256 signature:</p>

<div class="code-block-wrap">
    <div class="code-block-header">
        <span class="code-lang">Algorithm</span>
    </div>
    <pre><code>user_hash = HMAC-SHA256(site_api_key, raw_user_id)[:16]</code></pre>
</div>

<h3>Key Security & Privacy Guarantees:</h3>
<ul class="doc-list">
    <li><strong>Tenant Isolation:</strong> Even if two websites track the same user <code>alice@example.com</code>, their hashes are completely distinct and impossible to correlate across sites.</li>
    <li><strong>Zero Raw Storage:</strong> No personal emails or raw database IDs are persisted if you choose to hash the identifier.</li>
    <li><strong>Consistent User Journey:</strong> The hash is deterministic for a given site, allowing Hotspot to aggregate lifetime visits, returning user percentages, and customer retention.</li>
</ul>

<h2>Identifying Authenticated Users</h2>
<p>Call <code>hotspot.identify</code> upon user sign-in or session authentication:</p>

<div class="code-block-wrap">
    <div class="code-block-header">
        <span class="code-lang">JavaScript</span>
        <button class="doc-copy-btn" type="button" data-copy-target="code-identify">Copy</button>
    </div>
    <pre><code id="code-identify">// Identify customer upon login
hotspot.identify("user_9421_enterprise", {
  plan: "enterprise",
  role: "admin",
  company: "Acme Corp",
  cycle: "annual",
  value_tier: "vip"
}, { hash: true }); // enables zero-PII hashing</code></pre>
</div>

<h2>Account Switching & Logout Protection</h2>
<p>On shared computers or coworking environments, users often log out and another signs in within the same browser tab. To prevent mixing session data:</p>

<div class="code-block-wrap">
    <div class="code-block-header">
        <span class="code-lang">JavaScript</span>
        <button class="doc-copy-btn" type="button" data-copy-target="code-reset">Copy</button>
    </div>
    <pre><code id="code-reset">// Call upon user logout
hotspot.reset();</code></pre>
</div>
<p><code>hotspot.reset()</code> immediately purges cached traits from <code>localStorage</code>, rotates the session ID, and initializes a clean anonymous session.</p>
""",
    },
    "customer-journey": {
        "slug": "customer-journey",
        "category": "crm-intelligence",
        "title": "Customer Journey Drawer & Timeline",
        "nav_label": "Customer Journey",
        "badge": "Core",
        "description": "Inspect individual customer timelines: lifetime sessions, total active time, friction incidents, and direct replay video jumps.",
        "keywords": [
            "customer journey drawer",
            "user session history",
            "friction incident timeline",
            "crm session replay",
        ],
        "prev_slug": "crm-identity",
        "next_slug": "vip-radar",
        "content_html": """
<div class="doc-lead">
    Clicking any user across the Hotspot dashboard opens the <strong>Customer Journey Drawer</strong>, presenting their complete lifetime history and friction metrics.
</div>

<h2>What the Customer Journey Drawer Displays</h2>
<div class="doc-steps">
    <div class="doc-step">
        <div class="doc-step-num">1</div>
        <div class="doc-step-body">
            <h3>Profile & Badges</h3>
            <p>Displays customer initials avatar, user hash (<code>#5635a901</code>), plan tier (<code>FREE</code>, <code>PRO</code>, <code>ENTERPRISE</code>), role (<code>Admin</code>, <code>Member</code>), and company affiliation.</p>
        </div>
    </div>
    <div class="doc-step">
        <div class="doc-step-num">2</div>
        <div class="doc-step-body">
            <h3>Lifetime KPIs</h3>
            <p>Aggregates total visits across all time: <strong>Lifetime Sessions</strong>, <strong>Active Time</strong> (excluding idle background tabs), and total <strong>Friction Incidents</strong> (JavaScript errors, dead clicks, rage clicks, and payment declines).</p>
        </div>
    </div>
    <div class="doc-step">
        <div class="doc-step-num">3</div>
        <div class="doc-step-body">
            <h3>Chronological Session History</h3>
            <p>A reverse-chronological list of every session recorded for this user, showing entry/exit paths, duration, and direct <strong>"Watch Replay"</strong> buttons that open the session recording instantly.</p>
        </div>
    </div>
</div>
""",
    },
    "vip-radar": {
        "slug": "vip-radar",
        "category": "crm-intelligence",
        "title": "High-Value VIP Customer Radar",
        "nav_label": "VIP Customer Radar",
        "badge": "Intelligence",
        "description": "Automated radar that surfaces paying customers encountering errors, rage clicks, dead clicks, or declined payments.",
        "keywords": [
            "vip customer radar",
            "customer churn prevention",
            "failed payment telemetry",
            "saas error monitoring",
        ],
        "prev_slug": "customer-journey",
        "next_slug": "funnels",
        "content_html": """
<div class="doc-lead">
    High-value paying subscribers rarely report bugs—they simply churn. Hotspot's <strong>VIP Customer Radar</strong> proactively flags paying customers experiencing active friction.
</div>

<h2>Friction Triggers Monitored by the Radar</h2>
<ul class="doc-list">
    <li><strong>Declined Payments:</strong> Card declined or insufficient funds during plan upgrades or renewals.</li>
    <li><strong>JavaScript Exceptions:</strong> Uncaught errors blocking dashboard features or checkout buttons.</li>
    <li><strong>Dead Clicks:</strong> Clicking on unclickable or disabled elements repeatedly.</li>
    <li><strong>Rage Clicks:</strong> Rapid multi-clicking on unresponsive buttons.</li>
</ul>

<h2>Tracking Purchases & Payment Failures in the SDK</h2>
<p>To feed the VIP Radar with subscription and plan context, use the dedicated SDK hooks:</p>

<div class="code-block-wrap">
    <div class="code-block-header">
        <span class="code-lang">JavaScript</span>
        <button class="doc-copy-btn" type="button" data-copy-target="code-purchase-telemetry">Copy</button>
    </div>
    <pre><code id="code-purchase-telemetry">// Track successful upgrade / subscription purchase
hotspot.trackPurchase({
  plan: "enterprise",
  value: 499,
  cycle: "annual",
  order_id: "ord_10492"
});

// Track checkout failure (e.g. Stripe card decline)
hotspot.trackPaymentFailure({
  error_code: "card_declined",
  step: "stripe_card_element"
});

// Track pricing page hesitation dwell time
hotspot.trackHesitation({
  plan: "pro",
  dwell_seconds: 18
});</code></pre>
</div>
""",
    },
    "funnels": {
        "slug": "funnels",
        "category": "funnels-analytics",
        "title": "Conversion Funnels & Drop-Off Diagnostics",
        "nav_label": "Funnels & Drop-Offs",
        "badge": "Diagnostics",
        "description": "Configure multi-step conversion corridors and automatically diagnose why users dropped off with root-cause categorization.",
        "keywords": [
            "conversion funnel analytics",
            "dropoff root cause analysis",
            "why users drop off",
            "funnel dropoff replays",
        ],
        "prev_slug": "vip-radar",
        "next_slug": "breakage",
        "content_html": """
<div class="doc-lead">
    Knowing that 60% of users abandoned your funnel is only half the battle. Hotspot's <strong>Why Did Users Drop Off?</strong> diagnostic engine analyzes abandoned sessions to explain the exact root cause.
</div>

<h2>The 5 Friction Categories Detected</h2>
<div class="doc-table-wrap">
    <table class="doc-table">
        <thead>
            <tr>
                <th>Category</th>
                <th>Diagnostic Trigger</th>
                <th>Common Root Cause</th>
            </tr>
        </thead>
        <tbody>
            <tr>
                <td><strong>Immediate Bounces</strong></td>
                <td>&lt;5 seconds spent on step</td>
                <td>Unexpected paywall, slow page load, or misleading ad copy.</td>
            </tr>
            <tr>
                <td><strong>Dead Clicks</strong></td>
                <td>Clicks on inactive UI elements</td>
                <td>Disabled submit buttons without validation tooltips.</td>
            </tr>
            <tr>
                <td><strong>JavaScript Errors</strong></td>
                <td>Client-side exceptions</td>
                <td>Broken checkout scripts, API fetch failures, or null pointer bugs.</td>
            </tr>
            <tr>
                <td><strong>Rage Clicks</strong></td>
                <td>Rapid clicking (&gt;3 clicks/sec)</td>
                <td>Unresponsive forms or sluggish mobile buttons.</td>
            </tr>
            <tr>
                <td><strong>Price Hesitation</strong></td>
                <td>Long dwell on checkout</td>
                <td>Missing local currency, coupon code hunting, or hidden fees.</td>
            </tr>
        </tbody>
    </table>
</div>

<h2>One-Click Sample Replays</h2>
<p>Each diagnostic card in the Funnel view includes a <strong>"Watch sample replay →"</strong> button. Clicking it loads a session where that exact friction occurred, allowing your engineering and design teams to reproduce and fix the bug immediately.</p>
""",
    },
    "breakage": {
        "slug": "breakage",
        "category": "funnels-analytics",
        "title": "Breakage, Rage Clicks & Dead Clicks",
        "nav_label": "Breakage & Clicks",
        "badge": "Core",
        "description": "Detect where your website UI is broken: clicks that do nothing, rage clicks in frustration, and client-side JavaScript crashes.",
        "keywords": [
            "rage clicks tracking",
            "dead clicks detection",
            "javascript error telemetry",
            "broken ui analytics",
        ],
        "prev_slug": "funnels",
        "next_slug": "session-replay",
        "content_html": """
<div class="doc-lead">
    The <strong>Breakage</strong> tab aggregates every frustrating interaction across your web application so you can fix UX blockers before customers complain.
</div>

<h2>1. Dead Clicks</h2>
<p>A dead click occurs when a visitor clicks an interactive-looking element (or a button/link) that triggers no DOM mutation, navigation, or network request. Hotspot monitors DOM mutation observers and highlights elements with the highest dead click frequency.</p>

<h2>2. Rage Clicks</h2>
<p>A rage click occurs when a user clicks the same element 3 or more times in rapid succession (&lt;1 second intervals). This is the clearest visual signal of user frustration.</p>

<h2>3. JavaScript Errors & Web Vitals</h2>
<p>Hotspot intercepts <code>window.onerror</code> and unhandled promise rejections, recording the error message, source file, line number, and stack trace alongside the session recording.</p>
""",
    },
    "session-replay": {
        "slug": "session-replay",
        "category": "funnels-analytics",
        "title": "Session Replay & Privacy Engine",
        "nav_label": "Session Replay",
        "badge": "Core",
        "description": "How Hotspot captures and plays back high-fidelity DOM sessions with rrweb, inactivity skipping, network inspection, and privacy redaction.",
        "keywords": [
            "session replay open source",
            "rrweb player self hosted",
            "privacy first session recording",
            "dom mutation replay",
        ],
        "prev_slug": "breakage",
        "next_slug": "sdk-reference",
        "content_html": """
<div class="doc-lead">
    Hotspot uses a customized <code>rrweb</code> DOM mutation streaming engine to reconstruct user sessions pixel-for-pixel, including canvas interactions, SVG animations, and dynamic CSS changes.
</div>

<h2>Player Features</h2>
<ul class="doc-list">
    <li><strong>Inactivity Skipping:</strong> Automatically fast-forwards through idle periods (e.g. when a visitor switches tabs or steps away).</li>
    <li><strong>Speed Multipliers:</strong> Play sessions at <code>1x</code>, <code>2x</code>, <code>4x</code>, or <code>8x</code> speed.</li>
    <li><strong>DevTools Inspector Tabs:</strong> Switch between <strong>Events</strong> (chronological click/pageview feed), <strong>Console</strong> (logged warnings &amp; errors), and <strong>Network</strong> (intercepted API calls &amp; statuses).</li>
</ul>

<h2>Resolving Localhost & Broken Fonts</h2>
<div class="callout note">
    <div class="callout-icon">ℹ️</div>
    <div class="callout-body">
        <strong>Why do localhost recordings sometimes show broken fonts or missing styles?</strong>
        When developers test on <code>localhost:3000</code>, local CSS stylesheets and fonts are not reachable once the local dev server is stopped. On production domains, Hotspot fetches and renders styles from live public URLs seamlessly.
    </div>
</div>
""",
    },
    "sdk-reference": {
        "slug": "sdk-reference",
        "category": "sdk-api",
        "title": "JavaScript SDK API Reference",
        "nav_label": "SDK API Reference",
        "badge": "API",
        "description": "Complete documentation for window.hotspot methods, options, event attributes, and lifecycle hooks.",
        "keywords": [
            "hotspot sdk reference",
            "hotspot js api",
            "telemetry sdk documentation",
            "hotspot javascript methods",
        ],
        "prev_slug": "session-replay",
        "next_slug": "privacy-masking",
        "content_html": """
<div class="doc-lead">
    The <code>window.hotspot</code> object is automatically initialized when <code>hotspot.js</code> loads. All methods are safe to call synchronously.
</div>

<h2><code>hotspot.identify(distinctId, traits, options)</code></h2>
<p>Associates the current visitor and session with a user profile.</p>
<div class="code-block-wrap">
    <pre><code>hotspot.identify(distinctId: string, traits?: object, options?: { hash?: boolean })</code></pre>
</div>
<ul class="doc-list">
    <li><code>distinctId</code>: User identifier (database ID, UUID, or email).</li>
    <li><code>traits</code>: Key-value metadata (<code>plan</code>, <code>role</code>, <code>company</code>, <code>cycle</code>).</li>
    <li><code>options.hash</code>: When set to <code>true</code>, salts and hashes the ID with HMAC-SHA256.</li>
</ul>

<h2><code>hotspot.trackPurchase(details)</code></h2>
<p>Records a conversion or subscription event. Automatically purges card numbers and CVVs.</p>
<div class="code-block-wrap">
    <pre><code>hotspot.trackPurchase({
  plan: "pro",        // "free", "pro", "enterprise"
  value: 49,          // numeric price
  cycle: "monthly",   // "monthly" | "annual"
  order_id: "ord_101" // optional transaction ID
});</code></pre>
</div>

<h2><code>hotspot.trackPaymentFailure(details)</code></h2>
<p>Logs payment declines to alert the VIP Radar and drop-off diagnostics.</p>
<div class="code-block-wrap">
    <pre><code>hotspot.trackPaymentFailure({
  error_code: "card_declined",
  step: "checkout_submit"
});</code></pre>
</div>

<h2><code>hotspot.trackHesitation(details)</code></h2>
<p>Records friction or pricing dwell time before a user navigates away.</p>
<div class="code-block-wrap">
    <pre><code>hotspot.trackHesitation({
  plan: "enterprise",
  dwell_seconds: 24
});</code></pre>
</div>

<h2><code>hotspot.reset()</code></h2>
<p>Clears user identity, rotates the session ID, and resets visitor tracking on logout.</p>
<div class="code-block-wrap">
    <pre><code>hotspot.reset();</code></pre>
</div>
""",
    },
    "privacy-masking": {
        "slug": "privacy-masking",
        "category": "sdk-api",
        "title": "Privacy Controls & DOM Element Masking",
        "nav_label": "Privacy & Masking",
        "badge": "Security",
        "description": "How to mask sensitive inputs, passwords, credit card fields, and proprietary UI content from session recordings.",
        "keywords": [
            "session replay privacy masking",
            "redact sensitive data rrweb",
            "mask password fields",
            "zero pii session replay",
        ],
        "prev_slug": "sdk-reference",
        "next_slug": "mcp-ai",
        "content_html": """
<div class="doc-lead">
    Hotspot was architected from day one to guarantee that sensitive customer data never reaches the server.
</div>

<h2>Automatic Default Redactions</h2>
<ul class="doc-list">
    <li><strong>Password Inputs:</strong> All <code>&lt;input type="password"&gt;</code> values are completely omitted from recordings.</li>
    <li><strong>Credit Card Numbers &amp; CVVs:</strong> Stripped automatically by regex filters in both the client SDK and backend sanitizers.</li>
    <li><strong>Auth Tokens:</strong> Any property matching <code>token</code>, <code>secret</code>, <code>jwt</code>, or <code>password</code> is discarded.</li>
</ul>

<h2>Custom Element Masking</h2>
<p>To redact specific sensitive text or customer information on your page, add <code>data-hotspot-mask</code> or class <code>hotspot-mask</code> to the element:</p>

<div class="code-block-wrap">
    <div class="code-block-header">
        <span class="code-lang">HTML</span>
        <button class="doc-copy-btn" type="button" data-copy-target="code-mask-html">Copy</button>
    </div>
    <pre><code id="code-mask-html">&lt;!-- Text will be replaced with asterisks (***) in session replays --&gt;
&lt;div data-hotspot-mask&gt;
    Customer Account Balance: $48,250.00
&lt;/div&gt;

&lt;!-- Entire element block can be completely ignored --&gt;
&lt;div data-hotspot-ignore&gt;
    Internal Confidential Memo
&lt;/div&gt;</code></pre>
</div>
""",
    },
    "mcp-ai": {
        "slug": "mcp-ai",
        "category": "ai-ops",
        "title": "Model Context Protocol (MCP) & AI Agents",
        "nav_label": "AI & MCP Server",
        "badge": "AI",
        "description": "Connect Hotspot directly to Claude Code, Cursor, Antigravity, and Codex using the native Model Context Protocol (MCP) server.",
        "keywords": [
            "hotspot mcp server",
            "model context protocol analytics",
            "claude code session replay",
            "cursor mcp server",
            "antigravity telemetry skill",
        ],
        "prev_slug": "privacy-masking",
        "next_slug": "self-hosting",
        "content_html": """
<div class="doc-lead">
    Hotspot includes a native <strong>Model Context Protocol (MCP)</strong> server, allowing AI coding assistants (like Claude Code, Cursor, and Antigravity) to query your live telemetry, inspect session replays, and analyze drop-offs directly in your IDE.
</div>

<h2>MCP Endpoints</h2>
<ul class="doc-list">
    <li><strong>Streamable HTTP MCP:</strong> <code>https://hotspot.buxa.ai/mcp</code> (Modern standard)</li>
    <li><strong>Legacy Server-Sent Events (SSE):</strong> <code>https://hotspot.buxa.ai/mcp/sse</code></li>
    <li><strong>Downloadable Skill:</strong> <code>https://hotspot.buxa.ai/skill.md</code></li>
</ul>

<h2>Claude Desktop & Claude Code Configuration</h2>
<p>Add Hotspot to your <code>claude_desktop_config.json</code>:</p>

<div class="code-block-wrap">
    <div class="code-block-header">
        <span class="code-lang">JSON</span>
        <button class="doc-copy-btn" type="button" data-copy-target="code-claude-mcp">Copy</button>
    </div>
    <pre><code id="code-claude-mcp">{
  "mcpServers": {
    "hotspot": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-fetch", "https://hotspot.buxa.ai/mcp"],
      "headers": {
        "Authorization": "Bearer YOUR_HOTSPOT_SECRET_KEY"
      }
    }
  }
}</code></pre>
</div>

<h2>Cursor Configuration</h2>
<p>Add to <code>.cursor/mcp.json</code> in your project workspace:</p>

<div class="code-block-wrap">
    <div class="code-block-header">
        <span class="code-lang">JSON</span>
        <button class="doc-copy-btn" type="button" data-copy-target="code-cursor-mcp">Copy</button>
    </div>
    <pre><code id="code-cursor-mcp">{
  "mcpServers": {
    "hotspot": {
      "url": "https://hotspot.buxa.ai/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_HOTSPOT_SECRET_KEY"
      }
    }
  }
}</code></pre>
</div>

<h2>Instant Browser Connect (Device Flow)</h2>
<p>To authorize an AI tool without exposing raw passwords, visit <code>https://hotspot.buxa.ai/connect/</code>. Enter the 8-character device code displayed in your terminal, select which website to authorize, and approve access in one click.</p>
""",
    },
    "self-hosting": {
        "slug": "self-hosting",
        "category": "ai-ops",
        "title": "Self-Hosting, Docker & Production Ops",
        "nav_label": "Self-Hosting & Docker",
        "badge": "DevOps",
        "description": "Deploy Hotspot on your own VPS or Kubernetes cluster with Docker Compose, Redis persistence, and Caddy/Nginx reverse proxy.",
        "keywords": [
            "self host hotspot",
            "docker compose session replay",
            "self hosted web analytics docker",
            "redis session storage",
        ],
        "prev_slug": "mcp-ai",
        "next_slug": None,
        "content_html": """
<div class="doc-lead">
    Hotspot can be deployed anywhere Docker runs: AWS EC2, DigitalOcean, Hetzner, GCP, or on-premises servers.
</div>

<h2>1. <code>docker-compose.yml</code></h2>
<p>Save this file as <code>docker-compose.yml</code> on your server:</p>

<div class="code-block-wrap">
    <div class="code-block-header">
        <span class="code-lang">YAML</span>
        <button class="doc-copy-btn" type="button" data-copy-target="code-compose-yml">Copy</button>
    </div>
    <pre><code id="code-compose-yml">services:
  hotspot:
    image: hotspot:latest
    build:
      context: .
      dockerfile: products/hotspot/standalone/Dockerfile
    ports:
      - "127.0.0.1:8000:8000"
    env_file:
      - .env
    depends_on:
      redis:
        condition: service_healthy
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    command: ["redis-server", "--appendonly", "yes"]
    volumes:
      - hotspot-redis:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5
    restart: unless-stopped

volumes:
  hotspot-redis:</code></pre>
</div>

<h2>2. Environment Variables (<code>.env</code>)</h2>
<div class="doc-table-wrap">
    <table class="doc-table">
        <thead>
            <tr>
                <th>Variable</th>
                <th>Required</th>
                <th>Default / Description</th>
            </tr>
        </thead>
        <tbody>
            <tr>
                <td><code>HOTSPOT_SITE_URL</code></td>
                <td><span class="badge success">Yes</span></td>
                <td>Public URL (e.g. <code>https://hotspot.buxa.ai</code>).</td>
            </tr>
            <tr>
                <td><code>HOTSPOT_DASHBOARD_PASSWORD</code></td>
                <td>Recommended</td>
                <td>Master password to protect dashboard login.</td>
            </tr>
            <tr>
                <td><code>HOTSPOT_SECRET_KEY</code></td>
                <td><span class="badge success">Yes</span></td>
                <td>Server secret key used for session cookie signing and MCP auth.</td>
            </tr>
            <tr>
                <td><code>REDIS_URL</code></td>
                <td>Recommended</td>
                <td>Connection string (e.g. <code>redis://redis:6379/0</code>). Uses in-memory storage if unset.</td>
            </tr>
            <tr>
                <td><code>HOTSPOT_REPLAY_ENABLED</code></td>
                <td>Optional</td>
                <td>Set to <code>1</code> to enable session recording.</td>
            </tr>
            <tr>
                <td><code>HOTSPOT_DEMO_ENABLED</code></td>
                <td>Optional</td>
                <td>Set to <code>1</code> to expose the interactive sample site at <code>/demo/</code>.</td>
            </tr>
        </tbody>
    </table>
</div>

<h2>3. Start the Stack</h2>
<div class="code-block-wrap">
    <pre><code>docker compose up -d --build</code></pre>
</div>
<p>Your Hotspot server is now live on port 8000. Set up a reverse proxy (such as Caddy or Nginx) with an SSL certificate to serve over HTTPS.</p>
""",
    },
}


def get_doc_page(slug: str | None) -> dict[str, Any]:
    if not slug or slug not in DOCS_PAGES:
        return DOCS_PAGES["overview"]
    return DOCS_PAGES[slug]


def generate_doc_json_ld(doc: dict[str, Any], base_url: str) -> str:
    schema = {
        "@context": "https://schema.org",
        "@type": "TechArticle",
        "headline": doc["title"],
        "description": doc["description"],
        "url": f"{base_url.rstrip('/')}/docs/{doc['slug']}/",
        "author": {
            "@type": "Organization",
            "name": "Hotspot Team",
            "url": base_url,
        },
        "publisher": {
            "@type": "Organization",
            "name": "Hotspot",
            "url": base_url,
            "logo": {
                "@type": "ImageObject",
                "url": f"{base_url.rstrip('/')}/favicon.ico",
            },
        },
        "keywords": ", ".join(doc.get("keywords", [])),
        "inLanguage": "en-US",
    }
    return json.dumps(schema, separators=(",", ":"))


def generate_sitemap_xml(base_url: str) -> str:
    clean_base = base_url.rstrip("/")
    urls = [
        {"loc": f"{clean_base}/", "priority": "1.0", "changefreq": "daily"},
        {"loc": f"{clean_base}/docs/", "priority": "0.9", "changefreq": "weekly"},
    ]
    for slug in DOCS_PAGES:
        urls.append(
            {
                "loc": f"{clean_base}/docs/{slug}/",
                "priority": "0.85",
                "changefreq": "weekly",
            }
        )
    urls.extend(
        [
            {"loc": f"{clean_base}/demo/", "priority": "0.7", "changefreq": "monthly"},
            {"loc": f"{clean_base}/connect/", "priority": "0.6", "changefreq": "monthly"},
            {"loc": f"{clean_base}/dashboard/login/", "priority": "0.5", "changefreq": "monthly"},
        ]
    )

    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        lines.append(
            f"  <url><loc>{u['loc']}</loc><changefreq>{u['changefreq']}</changefreq><priority>{u['priority']}</priority></url>"
        )
    lines.append("</urlset>")
    return "\n".join(lines)


def generate_robots_txt(base_url: str) -> str:
    clean_base = base_url.rstrip("/")
    return f"""User-agent: *
Allow: /
Allow: /docs/
Allow: /demo/
Allow: /hotspot.js
Disallow: /hotspot/api/
Disallow: /hotspot/batch/
Disallow: /hotspot/replay/

Sitemap: {clean_base}/sitemap.xml
"""
