# Vendored rrweb bundles

Hotspot records and replays pages with [rrweb](https://github.com/rrweb-io/rrweb) (MIT, see `LICENSE-rrweb`).
The files here are the published UMD builds of version 2.2.0, unchanged apart from a license header and the removed source map comment, so neither server needs a JavaScript build step.

| File | Package | Served at | Used by |
| --- | --- | --- | --- |
| `rrweb-record.js` | `@rrweb/record` (`dist/record.umd.min.cjs`) | `/hotspot/assets/recorder.js` | `sdk/hotspot.js` loads it on visitors' pages when replay is on |
| `rrweb-replay.js` | `@rrweb/replay` (`dist/replay.umd.min.cjs`) | `/hotspot/assets/replay.js` | the dashboard player |
| `rrweb-replay.css` | `@rrweb/replay` (`dist/style.min.css`) | `/hotspot/assets/replay.css` | the dashboard player |

The globals are `window.rrwebRecord` (with `record`, `record.addCustomEvent`, `record.takeFullSnapshot`) and `window.rrwebReplay` (with `Replayer`).

## Upgrading

```bash
mkdir -p /tmp/rrweb && cd /tmp/rrweb && npm init -y >/dev/null
npm install --ignore-scripts @rrweb/record@<version> @rrweb/replay@<version>
```

Copy the three dist files over the ones here, keep the header comment, drop the trailing `sourceMappingURL` comment, update the version in this file and in `backend/service.py` if the served names change, then run the Hotspot tests and open a recording in the dashboard.
Check the recorder options the SDK passes (`maskInputFn`, `maskTextSelector`, `blockSelector`, `ignoreSelector`, `sampling`, `checkoutEveryNms`) still exist in the new version's record options.
