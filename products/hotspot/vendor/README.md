# Vendored assets

Hotspot records and replays pages with [rrweb](https://github.com/rrweb-io/rrweb) (MIT, see `LICENSE-rrweb`).
The rrweb files here are the published UMD builds of version 2.2.0, unchanged apart from a license header and the removed source map comment, so neither server needs a JavaScript build step.

| File | Package | Served at | Used by |
| --- | --- | --- | --- |
| `rrweb-record.js` | `@rrweb/record` (`dist/record.umd.min.cjs`) | `/hotspot/assets/recorder.js` | `sdk/hotspot.js` loads it on visitors' pages when replay is on |
| `rrweb-replay.js` | `@rrweb/replay` (`dist/replay.umd.min.cjs`) | `/hotspot/assets/replay.js` | the dashboard player |
| `rrweb-replay.css` | `@rrweb/replay` (`dist/style.min.css`) | `/hotspot/assets/replay.css` | the dashboard player |
| `world-map.json` | derived from this repository's `countryVectors.tsx` | `/hotspot/assets/world.json` | the dashboard's realtime map |

The globals are `window.rrwebRecord` (with `record`, `record.addCustomEvent`, `record.takeFullSnapshot`) and `window.rrwebReplay` (with `Replayer`).

## World map

`world-map.json` holds one SVG path per country in a `0 0 2754 1200` Robinson projection, keyed by ISO 3166 code, plus a `centers` map with the centroid of each country's largest polygon for markers.
It is generated from `products/product_analytics/frontend/insights/trends/WorldMap/countryVectors.tsx` (itself adapted from Wikimedia's public domain BlankMap-World.svg): curves are flattened, outlines simplified to about 1 unit, and coordinates rounded to one decimal, which brings 1.1 MB of JSX down to about 150 KB of JSON.
Microstates too small to draw keep their center so a visitor from them still gets a dot.

## Upgrading

```bash
mkdir -p /tmp/rrweb && cd /tmp/rrweb && npm init -y >/dev/null
npm install --ignore-scripts @rrweb/record@<version> @rrweb/replay@<version>
```

Copy the three dist files over the ones here, keep the header comment, drop the trailing `sourceMappingURL` comment, update the version in this file and in `backend/service.py` if the served names change, then run the Hotspot tests and open a recording in the dashboard.
Check the recorder options the SDK passes (`maskInputFn`, `maskTextSelector`, `blockSelector`, `ignoreSelector`, `sampling`, `checkoutEveryNms`) still exist in the new version's record options.
