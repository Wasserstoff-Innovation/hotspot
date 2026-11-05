"""A placeholder renderer for the standalone server.

The page templates use only ``{{ name }}`` placeholders, so Django's engine and this one
render the same files. Every value is HTML-escaped.
"""

from __future__ import annotations

import re
import html
from collections.abc import Mapping
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "hotspot"
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def render_string(source: str, context: Mapping[str, object]) -> str:
    def substitute(match: re.Match[str]) -> str:
        value = context.get(match.group(1))
        return "" if value is None else html.escape(str(value), quote=True)

    return _PLACEHOLDER.sub(substitute, source)


def render_template(name: str, context: Mapping[str, object]) -> str:
    return render_string((TEMPLATES_DIR / name).read_text(encoding="utf-8"), context)
