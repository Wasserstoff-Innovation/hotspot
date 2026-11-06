"""Country lookup from a browser timezone, for servers without a CDN country header.

The tz database ships two tables: ``zone.tab`` lists one country per zone and ``zone1970.tab`` lists
every country a zone covers, principal country first. Browsers still report legacy names such as
``Asia/Calcutta``, which only ``tzdata.zi`` maps to the zone the tables know.
"""

from __future__ import annotations

import zoneinfo
from collections.abc import Iterable
from functools import lru_cache
from importlib import resources
from pathlib import Path

_TAB_FILES = ("zone.tab", "zone1970.tab")


@lru_cache(maxsize=1)
def _tables() -> tuple[dict[str, str], dict[str, str]]:
    """The zone-to-country map and the link-to-zone map, read once from the first tz database found."""
    countries: dict[str, str] = {}
    links: dict[str, str] = {}
    for base in _database_dirs():
        for name in _TAB_FILES:
            for line in _read_lines(base, name):
                fields = line.split("\t")
                if len(fields) < 3:
                    continue
                code = fields[0].split(",")[0].strip().upper()
                zone = fields[2].strip()
                if len(code) == 2 and code.isalpha() and zone:
                    countries.setdefault(zone, code)
        for line in _read_lines(base, "tzdata.zi"):
            if line.startswith("L "):
                parts = line.split()
                if len(parts) == 3:
                    links.setdefault(parts[2], parts[1])
        if countries:
            break
    return countries, links


def _database_dirs() -> Iterable[Path]:
    try:
        package_dir = Path(str(resources.files("tzdata") / "zoneinfo"))
    except (ModuleNotFoundError, TypeError):
        package_dir = None
    candidates = [package_dir] if package_dir else []
    candidates.extend(Path(entry) for entry in zoneinfo.TZPATH)
    return [path for path in candidates if path.is_dir()]


def _read_lines(base: Path, name: str) -> list[str]:
    path = base / name
    try:
        with path.open(encoding="utf-8") as handle:
            return [line.rstrip("\n") for line in handle if line and not line.startswith("#")]
    except OSError:
        return []


def country_for_timezone(name: object) -> str | None:
    """The ISO 3166 code for a timezone name, or None when the name is unknown or no database is installed."""
    if not isinstance(name, str) or not name.strip():
        return None
    countries, links = _tables()
    zone = name.strip()
    return countries.get(zone) or countries.get(links.get(zone, ""))
