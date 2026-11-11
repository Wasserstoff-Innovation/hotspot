"""Run the Hotspot MCP server over stdio (`python -m products.hotspot.mcp`)."""

from __future__ import annotations

import os
import argparse
from collections.abc import Sequence

from products.hotspot.backend.mcp import run_stdio_server
from products.hotspot.backend.service import HotspotService


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hotspot MCP stdio server")
    parser.add_argument(
        "--secret-key",
        default=os.environ.get("HOTSPOT_MCP_SECRET_KEY", "").strip() or None,
        help="Optional site Secret Key (hs_sec_...) or admin password for scoped analytics access.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    service = HotspotService.from_env()
    run_stdio_server(service=service, default_secret_key=args.secret_key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
