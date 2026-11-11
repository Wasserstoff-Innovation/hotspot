"""Everything the dashboard shows, computed from stored session summaries and events."""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from products.hotspot.backend.config import HotspotConfig
from products.hotspot.backend.store import SessionStore

ACTIVE_WINDOW_SECONDS = 300
REALTIME_WINDOW_SECONDS = 1800
BOUNCE_SECONDS = 15
MAX_SESSIONS_ANALYZED = 1000
MAX_LIST_ROWS = 25
TREND_MAX_BUCKETS = 91
FLOW_DEPTH = 5
FLOW_NODES_PER_STEP = 6
STATUS_FILTERS = frozenset(
    {"all", "converted", "rage", "error", "bounced", "engaged", "active", "identified", "dead", "broken"}
)
STEP_KINDS = frozenset({"path", "event", "click", "text"})
CLICK_EVENTS = frozenset({"$autocapture", "$rageclick"})
# Referrer hosts are grouped into the channels a marketing report uses. A label match ("google" in
# www.google.co.uk) covers the engines with many country domains; the exact domains are too short
# or too generic to match by label.
SEARCH_LABELS = frozenset(
    {"google", "bing", "duckduckgo", "yahoo", "baidu", "yandex", "ecosia", "qwant", "naver", "seznam", "startpage"}
)
SEARCH_DOMAINS = ("search.brave.com", "ask.com")
SOCIAL_LABELS = frozenset(
    {
        "facebook",
        "instagram",
        "twitter",
        "linkedin",
        "reddit",
        "youtube",
        "tiktok",
        "pinterest",
        "threads",
        "mastodon",
        "snapchat",
        "whatsapp",
        "telegram",
        "discord",
        "quora",
        "tumblr",
    }
)
SOCIAL_DOMAINS = ("x.com", "t.co", "fb.com", "youtu.be", "bsky.app", "lnkd.in")
PAID_MEDIUMS = frozenset(
    {"cpc", "ppc", "paid", "paidsearch", "paid_search", "paid_social", "paidsocial", "display", "cpm", "retargeting"}
)
EMAIL_MEDIUMS = frozenset({"email", "e-mail", "newsletter"})
CHANNEL_ORDER = ("direct", "search", "social", "ai", "email", "paid", "campaign", "referral")
# Each web vital with the $web_vitals property it arrives in and the decimals it is reported with.
# CLS is a unitless score around 0.1, so it keeps three decimals; the rest are milliseconds.
WEB_VITALS: tuple[tuple[str, str, int], ...] = (
    ("lcp_ms", "$lcp_ms", 0),
    ("cls", "$cls", 3),
    ("inp_ms", "$inp_ms", 0),
    ("fcp_ms", "$fcp_ms", 0),
    ("ttfb_ms", "$ttfb_ms", 0),
)


@dataclass(frozen=True)
class FunnelStep:
    spec: str
    kind: str
    value: str
    label: str


@dataclass(frozen=True)
class OverviewQuery:
    search: str = ""
    status: str = "all"
    device_type: str = "all"
    browser: str = "all"
    os: str = "all"
    site: str = "all"
    hours: int = 0
    limit: int = 200
    funnel: tuple[str, ...] = ()
    conversion_event: str | None = None
    # The viewer's offset from UTC in minutes, so chart buckets start on their hours and days.
    tz_offset: int = 0

    @classmethod
    def from_params(cls, params: Mapping[str, str]) -> OverviewQuery:
        status = (params.get("status") or "all").strip().lower()
        funnel_raw = params.get("funnel") or ""
        return cls(
            search=(params.get("search") or "").strip()[:200],
            status=status if status in STATUS_FILTERS else "all",
            device_type=(params.get("device_type") or "all").strip().lower()[:40] or "all",
            browser=(params.get("browser") or "all").strip().lower()[:40] or "all",
            os=(params.get("os") or "all").strip().lower()[:40] or "all",
            site=(params.get("site") or "all").strip().lower()[:100] or "all",
            hours=_bounded_int(params.get("hours"), default=0, low=0, high=24 * 90),
            limit=_bounded_int(params.get("limit"), default=200, low=1, high=MAX_SESSIONS_ANALYZED),
            funnel=tuple(step.strip() for step in funnel_raw.split("|") if step.strip())[:8],
            conversion_event=(params.get("conversion_event") or "").strip()[:200] or None,
            tz_offset=_bounded_int(params.get("tz_offset"), default=0, low=-14 * 60, high=14 * 60),
        )


def _bounded_int(value: str | None, default: int, low: int, high: int) -> int:
    try:
        return min(high, max(low, int(value))) if value else default
    except ValueError:
        return default


def session_status(summary: Mapping[str, Any], now: float) -> str:
    if summary.get("converted"):
        return "converted"
    if summary.get("rage_click_count"):
        return "rage"
    if summary.get("error_count"):
        return "error"
    if now - float(summary.get("last_seen") or 0) < ACTIVE_WINDOW_SECONDS:
        return "active"
    bounced = (
        int(summary.get("pageview_count") or 0) <= 1
        and not summary.get("click_count")
        and int(summary.get("duration_seconds") or 0) < BOUNCE_SECONDS
    )
    return "bounced" if bounced else "engaged"


def session_label(summary: Mapping[str, Any]) -> str:
    identity = summary.get("identity") or {}
    for key in ("name", "email"):
        value = identity.get(key)
        if value:
            return str(value)
    distinct_id = str(summary.get("distinct_id") or "")
    if distinct_id and distinct_id != "anonymous" and "@" in distinct_id:
        return distinct_id
    session_id = str(summary.get("session_id") or "")
    return f"Visitor {session_id[-6:]}" if session_id else "Visitor"


def format_session(summary: Mapping[str, Any], now: float) -> dict[str, Any]:
    identity = summary.get("identity") or {}
    return {
        "session_id": summary.get("session_id"),
        "distinct_id": summary.get("distinct_id"),
        "label": session_label(summary),
        "identity": identity or None,
        "status": session_status(summary, now),
        "first_seen": summary.get("first_seen"),
        "last_seen": summary.get("last_seen"),
        "seconds_ago": max(0, int(now - float(summary.get("last_seen") or now))),
        "duration_seconds": int(summary.get("duration_seconds") or 0),
        "active_seconds": int(summary.get("active_seconds") or 0),
        "event_count": int(summary.get("event_count") or 0),
        "pageview_count": int(summary.get("pageview_count") or 0),
        "click_count": int(summary.get("click_count") or 0),
        "rage_click_count": int(summary.get("rage_click_count") or 0),
        "dead_click_count": int(summary.get("dead_click_count") or 0),
        "form_submit_count": int(summary.get("form_submit_count") or 0),
        "error_count": int(summary.get("error_count") or 0),
        "custom_event_count": int(summary.get("custom_event_count") or 0),
        "pages": list(summary.get("pages") or []),
        "entry_page": summary.get("entry_page"),
        "exit_page": summary.get("exit_page"),
        "entry_url": summary.get("entry_url"),
        "entry_title": summary.get("entry_title"),
        "referrer": summary.get("referrer"),
        "referrer_host": summary.get("referrer_host"),
        "is_ai_referral": bool(summary.get("is_ai_referral")),
        "ai_source": summary.get("ai_source"),
        "utm": summary.get("utm") or {},
        "browser": summary.get("browser"),
        "browser_version": summary.get("browser_version"),
        "os": summary.get("os"),
        "device_type": summary.get("device_type"),
        "country": summary.get("country"),
        "viewport_width": summary.get("viewport_width"),
        "viewport_height": summary.get("viewport_height"),
        "language": summary.get("language"),
        "timezone": summary.get("timezone"),
        "max_scroll_depth": summary.get("max_scroll_depth"),
        "converted": bool(summary.get("converted")),
        "conversion_event": summary.get("conversion_event"),
        "last_event": summary.get("last_event"),
        "has_recording": int(summary.get("recording_chunks") or 0) > 0,
        "recording_events": int(summary.get("recording_events") or 0),
        "recording_started_at": summary.get("recording_started_at"),
        "recording_truncated": bool(summary.get("recording_truncated")),
    }


def parse_funnel_steps(specs: Sequence[str], config: HotspotConfig) -> list[FunnelStep]:
    steps = [step for step in (parse_funnel_step(spec) for spec in specs) if step is not None]
    if steps:
        return steps
    conversion = sorted(config.conversion_events)[0] if config.conversion_events else "$form_submit"
    return [
        parse_funnel_step("event:$pageview") or _step("event", "$pageview"),
        parse_funnel_step("event:$autocapture") or _step("event", "$autocapture"),
        parse_funnel_step(f"event:{conversion}") or _step("event", conversion),
    ]


def parse_funnel_step(spec: str) -> FunnelStep | None:
    spec = spec.strip()
    if not spec:
        return None
    kind, separator, value = spec.partition(":")
    if not separator or kind not in STEP_KINDS:
        kind, value = ("path", spec) if spec.startswith("/") else ("event", spec)
    value = value.strip()
    if not value:
        return None
    return _step(kind, value)


def _step(kind: str, value: str) -> FunnelStep:
    labels = {
        "$pageview": "Viewed a page",
        "$autocapture": "Clicked anything",
        "$form_submit": "Submitted a form",
        "$rageclick": "Rage clicked",
        "$exception": "Hit a JavaScript error",
        "$identify": "Was identified",
    }
    if kind == "path":
        label = f"Visited {value}"
    elif kind == "click":
        label = f"Clicked {value}"
    elif kind == "text":
        label = f'Clicked "{value}"'
    else:
        label = labels.get(value, f"Event {value}")
    return FunnelStep(spec=f"{kind}:{value}", kind=kind, value=value, label=label)


def step_matches(step: FunnelStep, event: Mapping[str, Any]) -> bool:
    name = event.get("event")
    props = event.get("properties") or {}
    if step.kind == "event":
        return name == step.value
    if step.kind == "path":
        return name == "$pageview" and _path_matches(step.value, props.get("$pathname"))
    if name not in CLICK_EVENTS:
        return False
    if step.kind == "click":
        selector = str(props.get("$el_selector") or "")
        element_id = str(props.get("$el_id") or "")
        wanted = step.value
        if wanted.endswith("*"):
            return selector.startswith(wanted[:-1])
        return selector == wanted or (wanted.startswith("#") and element_id == wanted[1:])
    return step.value.lower() in str(props.get("$el_text") or "").lower()


def _path_matches(wanted: str, pathname: Any) -> bool:
    if not isinstance(pathname, str):
        return False
    if wanted.endswith("*"):
        return pathname.startswith(wanted[:-1])
    return pathname.rstrip("/") == wanted.rstrip("/")


def compute_funnel(
    sessions: Iterable[tuple[Mapping[str, Any], Sequence[Mapping[str, Any]]]], steps: Sequence[FunnelStep]
) -> dict[str, Any]:
    reached = [0] * len(steps)
    gaps: list[list[float]] = [[] for _ in steps]
    dropoff_session_ids: list[list[str]] = [[] for _ in steps]
    total = 0
    for summary, events in sessions:
        total += 1
        index = 0
        previous_ts: float | None = None
        sid = str(summary.get("session_id") or "")
        for event in sorted(events, key=lambda item: float(item.get("timestamp") or 0)):
            if index >= len(steps):
                break
            if step_matches(steps[index], event):
                timestamp = float(event.get("timestamp") or 0)
                if previous_ts is not None:
                    gaps[index - 1].append(max(0.0, timestamp - previous_ts))
                previous_ts = timestamp
                reached[index] += 1
                index += 1
        if 0 < index < len(steps):
            if sid and len(dropoff_session_ids[index - 1]) < 30:
                dropoff_session_ids[index - 1].append(sid)

    rows: list[dict[str, Any]] = []
    first = reached[0] if reached else 0
    for position, step in enumerate(steps):
        count = reached[position]
        previous = reached[position - 1] if position else count
        conv_from_prev = round(count / previous * 100, 1) if previous else 0.0
        dropoff_sessions = dropoff_session_ids[position - 1] if position else []
        rows.append(
            {
                "index": position + 1,
                "spec": step.spec,
                "kind": step.kind,
                "value": step.value,
                "label": step.label,
                "count": count,
                "pct_of_first": round(count / first * 100, 1) if first else 0.0,
                "conv_from_prev": conv_from_prev if position else 100.0,
                "dropoff_from_prev": round(100 - conv_from_prev, 1) if position else 0.0,
                "dropoff_count": max(0, previous - count) if position else 0,
                "dropoff_sessions": dropoff_sessions,
                "median_seconds_to_next": round(statistics.median(gaps[position]), 1) if gaps[position] else None,
            }
        )
    last = reached[-1] if reached else 0
    return {
        "steps": rows,
        "sessions_analyzed": total,
        "conversion_rate": round(last / first * 100, 1) if first else 0.0,
    }


def compute_paths(summaries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    entries: Counter[str] = Counter()
    exits: Counter[str] = Counter()
    transitions: Counter[tuple[str, str]] = Counter()
    outgoing: Counter[str] = Counter()
    paths: Counter[tuple[str, ...]] = Counter()
    converted_by_path: Counter[tuple[str, ...]] = Counter()
    sequences: list[tuple[list[str], bool]] = []
    for summary in summaries:
        pages = [page for page in (summary.get("pages") or []) if isinstance(page, str)]
        if not pages:
            continue
        sequences.append((pages, bool(summary.get("converted"))))
        entries[pages[0]] += 1
        exits[pages[-1]] += 1
        for source, target in zip(pages, pages[1:]):
            transitions[(source, target)] += 1
            outgoing[source] += 1
        key = tuple(pages[:3])
        paths[key] += 1
        if summary.get("converted"):
            converted_by_path[key] += 1

    total_entries = sum(entries.values())
    return {
        "flow": compute_flow(sequences),
        "entry_pages": [
            {"pathname": page, "count": count, "share": _share(count, total_entries)}
            for page, count in entries.most_common(MAX_LIST_ROWS)
        ],
        "exit_pages": [
            {"pathname": page, "count": count, "share": _share(count, total_entries)}
            for page, count in exits.most_common(MAX_LIST_ROWS)
        ],
        "transitions": [
            {"from": source, "to": target, "count": count, "share": _share(count, outgoing[source])}
            for (source, target), count in transitions.most_common(MAX_LIST_ROWS)
        ],
        "top_paths": [
            {
                "pages": list(key),
                "count": count,
                "share": _share(count, total_entries),
                "converted": converted_by_path[key],
            }
            for key, count in paths.most_common(MAX_LIST_ROWS)
        ],
    }


def compute_suggested_funnels(
    paths_data: Mapping[str, Any],
    pages_data: Sequence[Mapping[str, Any]],
    config: HotspotConfig,
) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    top_paths = paths_data.get("top_paths") or []
    entry_pages = paths_data.get("entry_pages") or []
    conversion = sorted(config.conversion_events)[0] if config.conversion_events else "$form_submit"

    # 1. Top Discovered Multi-page Journey
    multi_page_paths = [p for p in top_paths if len(p.get("pages") or []) >= 2]
    if multi_page_paths:
        best_path = multi_page_paths[0]
        pages = best_path["pages"][:4]
        steps = [{"kind": "path", "value": p, "label": f"Visited {p}"} for p in pages]
        path_name = " → ".join(pages)
        suggestions.append(
            {
                "id": "top_journey",
                "title": f"⚡ Discovered Flow ({path_name})",
                "badge": "Top Journey",
                "description": f"Discovered from {best_path.get('count', 0)} sessions ({best_path.get('share', 0)}% of traffic)",
                "steps": steps,
            }
        )

    # 2. Key Pages Corridor (Entry -> Destination -> Goal)
    if entry_pages:
        entry = entry_pages[0]["pathname"]
        other_pages = [p["pathname"] for p in pages_data if p.get("pathname") != entry]
        steps = [{"kind": "path", "value": entry, "label": f"Visited {entry}"}]
        if other_pages:
            steps.append({"kind": "path", "value": other_pages[0], "label": f"Visited {other_pages[0]}"})
            if len(other_pages) > 1:
                steps.append({"kind": "path", "value": other_pages[1], "label": f"Visited {other_pages[1]}"})
            else:
                steps.append({"kind": "event", "value": conversion, "label": _step("event", conversion).label})
        else:
            steps.append({"kind": "event", "value": "$autocapture", "label": "Clicked anything"})
            steps.append({"kind": "event", "value": conversion, "label": _step("event", conversion).label})

        name = " → ".join(s["value"] for s in steps)
        suggestions.append(
            {
                "id": "core_pages",
                "title": f"📄 Core Flow ({name})",
                "badge": "Traffic Flow",
                "description": "Top entry page and key destination pages",
                "steps": steps,
            }
        )

    # 3. Action / Engagement Funnel
    first_page = entry_pages[0]["pathname"] if entry_pages else (pages_data[0]["pathname"] if pages_data else "/")
    suggestions.append(
        {
            "id": "interaction",
            "title": f"🖱️ Interaction ({first_page} → Click → Goal)",
            "badge": "Interaction",
            "description": "Visitor arrives on key page, interacts with element, and reaches goal",
            "steps": [
                {"kind": "path", "value": first_page, "label": f"Visited {first_page}"},
                {"kind": "event", "value": "$autocapture", "label": "Clicked anything"},
                {"kind": "event", "value": conversion, "label": _step("event", conversion).label},
            ],
        }
    )

    # 4. Standard Generic Baseline
    suggestions.append(
        {
            "id": "standard",
            "title": "📊 Standard Baseline",
            "badge": "Baseline",
            "description": "Viewed any page → Clicked anything → Form submit",
            "steps": [
                {"kind": "event", "value": "$pageview", "label": "Viewed any page"},
                {"kind": "event", "value": "$autocapture", "label": "Clicked anything"},
                {"kind": "event", "value": conversion, "label": _step("event", conversion).label},
            ],
        }
    )

    return suggestions


def compute_flow(sequences: Sequence[tuple[Sequence[str], bool]]) -> dict[str, Any]:
    """Page sequences as columns of nodes with the moves between them, the shape a flow diagram draws.

    Each column keeps its busiest pages and folds the rest into one "other" node, so a site with
    hundreds of URLs still draws a readable picture. A session that has no next page leaves the site
    at that node, which is what the diagram shows as drop-off.
    """
    steps: list[list[dict[str, Any]]] = []
    positions: list[dict[str, int]] = []
    for depth in range(FLOW_DEPTH):
        at_step: Counter[str] = Counter()
        exits: Counter[str] = Counter()
        converted: Counter[str] = Counter()
        for pages, did_convert in sequences:
            if len(pages) <= depth:
                continue
            page = pages[depth]
            at_step[page] += 1
            if len(pages) == depth + 1:
                exits[page] += 1
            if did_convert:
                converted[page] += 1
        if not at_step:
            break
        kept = [page for page, _count in at_step.most_common(FLOW_NODES_PER_STEP)]
        nodes = [
            {
                "pathname": page,
                "count": at_step[page],
                "exits": exits[page],
                "converted": converted[page],
                "other": False,
            }
            for page in kept
        ]
        rest = [page for page in at_step if page not in kept]
        if rest:
            nodes.append(
                {
                    "pathname": None,
                    "count": sum(at_step[page] for page in rest),
                    "exits": sum(exits[page] for page in rest),
                    "converted": sum(converted[page] for page in rest),
                    "other": True,
                }
            )
        index = {page: position for position, page in enumerate(kept)}
        other_index = len(kept)
        positions.append({page: index.get(page, other_index) for page in at_step})
        steps.append(nodes)

    links: Counter[tuple[int, int, int]] = Counter()
    for pages, _did_convert in sequences:
        for depth in range(min(len(pages) - 1, len(steps) - 1)):
            links[(depth, positions[depth][pages[depth]], positions[depth + 1][pages[depth + 1]])] += 1
    return {
        "steps": steps,
        "links": [
            {"step": depth, "from": source, "to": target, "count": count}
            for (depth, source, target), count in sorted(links.items())
        ],
        "sessions": len(sequences),
    }


def compute_friction(
    sessions: Iterable[tuple[Mapping[str, Any], Sequence[Mapping[str, Any]]]],
) -> dict[str, list[dict[str, Any]]]:
    rage: dict[tuple[str, str, str], dict[str, Any]] = {}
    dead: dict[tuple[str, str, str], dict[str, Any]] = {}
    for summary, events in sessions:
        session_id = str(summary.get("session_id") or "")
        for event in events:
            name = event.get("event")
            props = event.get("properties") or {}
            if name == "$rageclick":
                bucket = rage
            elif name == "$autocapture" and props.get("$is_dead_click"):
                bucket = dead
            else:
                continue
            key = (
                str(props.get("$pathname") or ""),
                str(props.get("$el_selector") or props.get("$el_tag_name") or "unknown"),
                str(props.get("$el_text") or "")[:80],
            )
            row = bucket.setdefault(
                key,
                {
                    "pathname": key[0],
                    "selector": key[1],
                    "text": key[2],
                    "count": 0,
                    "sessions": set(),
                    "last_seen": 0.0,
                },
            )
            row["count"] += 1
            row["sessions"].add(session_id)
            row["last_seen"] = max(row["last_seen"], float(event.get("timestamp") or 0))
    return {"rage_clicks": _finish_friction(rage), "dead_clicks": _finish_friction(dead)}


def _finish_friction(bucket: Mapping[tuple[str, str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in bucket.values():
        finished = dict(row)
        finished["sessions"] = len(row["sessions"])
        rows.append(finished)
    rows.sort(key=lambda row: (row["count"], row["sessions"]), reverse=True)
    return rows[:MAX_LIST_ROWS]


def compute_web_vitals(
    sessions: Iterable[tuple[Mapping[str, Any], Sequence[Mapping[str, Any]]]],
) -> dict[str, Any]:
    """The 75th percentile of each vital, site-wide and per page, which is how Google scores them."""
    site: dict[str, list[float]] = defaultdict(list)
    per_page: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for _summary, events in sessions:
        for event in events:
            if event.get("event") != "$web_vitals":
                continue
            props = event.get("properties") or {}
            pathname = props.get("$pathname")
            for field, key, _digits in WEB_VITALS:
                value = props.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                    continue
                site[field].append(float(value))
                if isinstance(pathname, str) and pathname:
                    per_page[pathname][field].append(float(value))
    return {"site": _vitals_row(site), "pages": {path: _vitals_row(values) for path, values in per_page.items()}}


def _vitals_row(samples: Mapping[str, Sequence[float]]) -> dict[str, Any]:
    row: dict[str, Any] = {field: _p75(samples.get(field, ()), digits) for field, _key, digits in WEB_VITALS}
    row["samples"] = max((len(values) for values in samples.values()), default=0)
    return row


def _p75(values: Sequence[float], digits: int) -> float | int | None:
    if not values:
        return None
    ordered = sorted(values)
    value = ordered[max(0, math.ceil(0.75 * len(ordered)) - 1)]
    return round(value, digits) if digits else int(round(value))


def compute_pages(
    sessions: Iterable[tuple[Mapping[str, Any], Sequence[Mapping[str, Any]]]],
    page_vitals: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    views: Counter[str] = Counter()
    sessions_by_page: dict[str, set[str]] = defaultdict(set)
    converted_by_page: dict[str, set[str]] = defaultdict(set)
    rage: Counter[str] = Counter()
    dead: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    # A page can send several $pageleave events in one session (the tab was hidden and shown
    # again), so time on page is summed per session before it is averaged across sessions.
    seconds: dict[tuple[str, str], float] = defaultdict(float)
    depths: dict[tuple[str, str], float] = {}
    exits: Counter[str] = Counter()
    for summary, events in sessions:
        session_id = str(summary.get("session_id") or "")
        exit_page = summary.get("exit_page")
        if isinstance(exit_page, str):
            exits[exit_page] += 1
        for event in events:
            props = event.get("properties") or {}
            pathname = props.get("$pathname")
            if not isinstance(pathname, str) or not pathname:
                continue
            name = event.get("event")
            if name == "$pageview":
                views[pathname] += 1
                sessions_by_page[pathname].add(session_id)
                if summary.get("converted"):
                    converted_by_page[pathname].add(session_id)
            elif name == "$rageclick":
                rage[pathname] += 1
            elif name == "$autocapture" and props.get("$is_dead_click"):
                dead[pathname] += 1
            elif name == "$exception":
                errors[pathname] += 1
            elif name == "$pageleave":
                key = (session_id, pathname)
                duration = props.get("$duration_seconds")
                if isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration >= 0:
                    seconds[key] += float(duration)
                depth = props.get("$scroll_depth_percent")
                if isinstance(depth, (int, float)) and not isinstance(depth, bool):
                    depths[key] = max(depths.get(key, 0.0), float(depth))
    rows = []
    for pathname, count in views.most_common(MAX_LIST_ROWS):
        session_count = len(sessions_by_page[pathname])
        page_seconds = [value for (sid, path), value in seconds.items() if path == pathname]
        page_depths = [value for (sid, path), value in depths.items() if path == pathname]
        rows.append(
            {
                "pathname": pathname,
                "views": count,
                "sessions": session_count,
                "avg_seconds": round(statistics.mean(page_seconds), 1) if page_seconds else None,
                "avg_scroll_depth": round(statistics.mean(page_depths)) if page_depths else None,
                "exits": exits[pathname],
                "exit_rate": _share(exits[pathname], session_count),
                "converted": len(converted_by_page[pathname]),
                "conversion_rate": _share(len(converted_by_page[pathname]), session_count),
                "rage_clicks": rage[pathname],
                "dead_clicks": dead[pathname],
                "errors": errors[pathname],
                "vitals": dict((page_vitals or {}).get(pathname) or {}) or None,
            }
        )
    return rows


def compute_referrers(summaries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    sessions: Counter[str] = Counter()
    converted: Counter[str] = Counter()
    ai_sources: Counter[str] = Counter()
    ai_sessions = 0
    for summary in summaries:
        host = str(summary.get("referrer_host") or "direct")
        sessions[host] += 1
        if summary.get("converted"):
            converted[host] += 1
        if summary.get("is_ai_referral"):
            ai_sessions += 1
            ai_sources[str(summary.get("ai_source") or host)] += 1
    total = sum(sessions.values())
    return {
        "sources": [
            {
                "host": host,
                "sessions": count,
                "share": _share(count, total),
                "converted": converted[host],
                "conversion_rate": _share(converted[host], count),
            }
            for host, count in sessions.most_common(MAX_LIST_ROWS)
        ],
        "channels": compute_channels(summaries),
        "ai_sessions": ai_sessions,
        "ai_sources": [{"source": source, "sessions": count} for source, count in ai_sources.most_common(10)],
    }


def channel_for(summary: Mapping[str, Any]) -> str:
    if summary.get("is_ai_referral"):
        return "ai"
    utm = summary.get("utm") or {}
    medium = str(utm.get("utm_medium") or "").strip().lower()
    if medium in EMAIL_MEDIUMS:
        return "email"
    if medium in PAID_MEDIUMS or medium.startswith("paid"):
        return "paid"
    host = str(summary.get("referrer_host") or "").strip().lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return "campaign" if utm else "direct"
    labels = set(host.split("."))
    if labels & SEARCH_LABELS or _host_in(host, SEARCH_DOMAINS):
        return "search"
    if labels & SOCIAL_LABELS or _host_in(host, SOCIAL_DOMAINS):
        return "social"
    return "referral"


def _host_in(host: str, domains: Sequence[str]) -> bool:
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def compute_channels(summaries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    sessions: Counter[str] = Counter()
    converted: Counter[str] = Counter()
    for summary in summaries:
        channel = channel_for(summary)
        sessions[channel] += 1
        if summary.get("converted"):
            converted[channel] += 1
    total = sum(sessions.values())
    return [
        {
            "channel": channel,
            "sessions": sessions[channel],
            "share": _share(sessions[channel], total),
            "converted": converted[channel],
            "conversion_rate": _share(converted[channel], sessions[channel]),
        }
        for channel in CHANNEL_ORDER
        if sessions[channel]
    ]


def compute_errors(
    sessions: Iterable[tuple[Mapping[str, Any], Sequence[Mapping[str, Any]]]],
) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for summary, events in sessions:
        session_id = str(summary.get("session_id") or "")
        for event in events:
            if event.get("event") != "$exception":
                continue
            props = event.get("properties") or {}
            message = str(props.get("$exception_message") or "Unknown error")[:200]
            row = rows.setdefault(
                message,
                {
                    "message": message,
                    "type": str(props.get("$exception_type") or "Error")[:80],
                    "count": 0,
                    "sessions": set(),
                    "last_seen": 0.0,
                    "pathname": str(props.get("$pathname") or ""),
                },
            )
            row["count"] += 1
            row["sessions"].add(session_id)
            row["last_seen"] = max(row["last_seen"], float(event.get("timestamp") or 0))
    finished = []
    for row in rows.values():
        item = dict(row)
        item["sessions"] = len(row["sessions"])
        finished.append(item)
    finished.sort(key=lambda row: (row["count"], row["last_seen"]), reverse=True)
    return finished[:MAX_LIST_ROWS]


def compute_activity(
    recent_events: Sequence[Mapping[str, Any]], summaries: Sequence[Mapping[str, Any]], now: float
) -> dict[str, list[int]]:
    per_minute = [0] * 60
    for event in recent_events:
        age = now - float(event.get("timestamp") or 0)
        if 0 <= age < 3600:
            per_minute[59 - int(age // 60)] += 1
    per_hour = [0] * 24
    for summary in summaries:
        age = now - float(summary.get("first_seen") or 0)
        if 0 <= age < 86400:
            per_hour[23 - int(age // 3600)] += 1
    return {"events_per_minute": per_minute, "sessions_per_hour": per_hour}


def compute_trend(
    sessions: Iterable[tuple[Mapping[str, Any], Sequence[Mapping[str, Any]]]],
    hours: int,
    now: float,
    tz_offset_minutes: int = 0,
) -> dict[str, Any]:
    """Sessions, visitors, page views and conversions per time bucket across the selected range.

    Buckets align to the viewer's clock (whole hours or whole days), so the first and last buckets
    can be partial; one extra bucket keeps every session in the range on the chart.
    """
    rows = list(sessions)
    offset = tz_offset_minutes * 60
    span: float
    if hours == 1:
        bucket = 300
        span = 3600.0
    elif hours:
        bucket = 3600 if hours <= 48 else 86400
        span = hours * 3600.0
    else:
        earliest = min((float(summary.get("first_seen") or now) for summary, _events in rows), default=now)
        span = max(3600.0, now - earliest)
        bucket = 3600 if span <= 48 * 3600 else 86400
    end = math.floor((now + offset) / bucket) * bucket + bucket - offset
    count = min(TREND_MAX_BUCKETS, int(math.ceil(span / bucket)) + 1)
    start = end - count * bucket
    counts = {key: [0] * count for key in ("sessions", "pageviews", "converted")}
    visitors: list[set[str]] = [set() for _ in range(count)]

    def slot(timestamp: Any) -> int | None:
        try:
            moment = float(timestamp)
        except (TypeError, ValueError):
            return None
        if moment < start or moment >= end:
            return None
        return int((moment - start) // bucket)

    for summary, events in rows:
        index = slot(summary.get("first_seen"))
        if index is not None:
            counts["sessions"][index] += 1
            visitors[index].add(str(summary.get("distinct_id") or ""))
            if summary.get("converted"):
                counts["converted"][index] += 1
        for event in events:
            if event.get("event") != "$pageview":
                continue
            event_index = slot(event.get("timestamp"))
            if event_index is not None:
                counts["pageviews"][event_index] += 1
    return {
        "bucket_seconds": bucket,
        "start": start,
        "sessions": counts["sessions"],
        "visitors": [len(bucket_visitors) for bucket_visitors in visitors],
        "pageviews": counts["pageviews"],
        "converted": counts["converted"],
    }


def summarize_metrics(summaries: Sequence[Mapping[str, Any]], now: float) -> dict[str, Any]:
    """The headline numbers for a set of sessions, used for the range itself and the period before it."""
    total = len(summaries)
    converted = sum(1 for summary in summaries if summary.get("converted"))
    bounced = sum(1 for summary in summaries if session_status(summary, now) == "bounced")
    return {
        "sessions": total,
        "visitors": len({str(summary.get("distinct_id") or "") for summary in summaries}),
        "pageviews": sum(int(summary.get("pageview_count") or 0) for summary in summaries),
        "converted_sessions": converted,
        "conversion_rate": _share(converted, total),
        "bounce_rate": _share(bounced, total),
        "avg_duration_seconds": round(
            statistics.mean(int(summary.get("duration_seconds") or 0) for summary in summaries), 1
        )
        if summaries
        else 0.0,
    }


def compute_realtime(
    summaries: Sequence[Mapping[str, Any]], recent_events: Sequence[Mapping[str, Any]], now: float
) -> dict[str, Any]:
    """Who is on the site right now: the last 30 minutes regardless of the dashboard's time range."""
    recent = [s for s in summaries if now - float(s.get("last_seen") or 0) < REALTIME_WINDOW_SECONDS]
    countries: Counter[str] = Counter()
    pages: Counter[str] = Counter()
    for summary in recent:
        country = summary.get("country")
        if isinstance(country, str) and country:
            countries[country.upper()] += 1
        page = summary.get("exit_page")
        if isinstance(page, str) and page:
            pages[page] += 1
    minutes = REALTIME_WINDOW_SECONDS // 60
    per_minute: list[set[str]] = [set() for _ in range(minutes)]
    for event in recent_events:
        age = now - float(event.get("timestamp") or 0)
        if 0 <= age < REALTIME_WINDOW_SECONDS:
            per_minute[minutes - 1 - int(age // 60)].add(str(event.get("session_id") or ""))
    return {
        "window_seconds": REALTIME_WINDOW_SECONDS,
        "active_now": sum(1 for s in recent if now - float(s.get("last_seen") or 0) < ACTIVE_WINDOW_SECONDS),
        "active_in_window": len(recent),
        "countries": [{"country": code, "sessions": count} for code, count in countries.most_common(MAX_LIST_ROWS)],
        "pages": [{"pathname": page, "sessions": count} for page, count in pages.most_common(MAX_LIST_ROWS)],
        "sessions_per_minute": [len(ids) for ids in per_minute],
    }


def compute_breakdowns(summaries: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for field in ("device_type", "browser", "os", "country"):
        counter: Counter[str] = Counter(
            str(summary.get(field)) for summary in summaries if summary.get(field) not in (None, "")
        )
        result[field] = dict(counter.most_common(20))
    return result


def build_overview(
    store: SessionStore,
    query: OverviewQuery,
    config: HotspotConfig,
    now: float,
    authorized_scope: str = "*",
) -> dict[str, Any]:
    if authorized_scope != "*":
        query = OverviewQuery(
            hours=query.hours,
            site=authorized_scope,
            device_type=query.device_type,
            browser=query.browser,
            os=query.os,
            status=query.status,
            search=query.search,
            funnel=query.funnel,
            conversion_event=query.conversion_event,
            limit=query.limit,
            tz_offset=query.tz_offset,
        )
    stored = store.sessions(limit=MAX_SESSIONS_ANALYZED)
    in_range = stored
    if query.hours:
        cutoff = now - query.hours * 3600
        in_range = [summary for summary in stored if float(summary.get("last_seen") or 0) >= cutoff]
    summaries = [summary for summary in in_range if _matches_scope(summary, query)]

    with_events = [(summary, store.session_events(str(summary.get("session_id")))) for summary in summaries]
    visible = [summary for summary in summaries if _matches_session_filter(summary, query, now)]
    vitals = compute_web_vitals(with_events)

    steps = parse_funnel_steps(query.funnel, config)
    if query.conversion_event:
        steps = [*steps[:-1], _step("event", query.conversion_event)] if query.funnel else steps
    funnel = compute_funnel(with_events, steps)

    depths = [summary["max_scroll_depth"] for summary in summaries if summary.get("max_scroll_depth") is not None]
    with_rage = sum(1 for summary in summaries if summary.get("rage_click_count"))
    with_dead = sum(1 for summary in summaries if summary.get("dead_click_count"))
    with_errors = sum(1 for summary in summaries if summary.get("error_count"))
    metrics = {
        **summarize_metrics(summaries, now),
        "filtered_sessions": len(visible),
        "active_sessions": sum(1 for summary in summaries if now - float(summary.get("last_seen") or 0) < 300),
        "identified_sessions": sum(1 for summary in summaries if summary.get("identity")),
        "clicks": sum(int(summary.get("click_count") or 0) for summary in summaries),
        "rage_clicks": sum(int(summary.get("rage_click_count") or 0) for summary in summaries),
        "dead_clicks": sum(int(summary.get("dead_click_count") or 0) for summary in summaries),
        "form_submits": sum(int(summary.get("form_submit_count") or 0) for summary in summaries),
        "errors": sum(int(summary.get("error_count") or 0) for summary in summaries),
        "sessions_with_rage": with_rage,
        "sessions_with_dead_clicks": with_dead,
        "sessions_with_errors": with_errors,
        "sessions_with_breakage": sum(
            1
            for summary in summaries
            if summary.get("rage_click_count") or summary.get("dead_click_count") or summary.get("error_count")
        ),
        "avg_scroll_depth": round(statistics.mean(depths)) if depths else None,
        "ai_referral_sessions": sum(1 for summary in summaries if summary.get("is_ai_referral")),
        "sessions_with_replay": sum(1 for summary in summaries if int(summary.get("recording_chunks") or 0) > 0),
    }
    comparison = None
    if query.hours:
        window = query.hours * 3600
        previous = [
            summary
            for summary in stored
            if now - 2 * window <= float(summary.get("last_seen") or 0) < now - window
            and _matches_scope(summary, query)
        ]
        comparison = summarize_metrics(previous, now)
    recent_events = store.recent_events(limit=config.max_events)

    all_sites = store.sites()
    if authorized_scope != "*":
        target = authorized_scope.lower()
        all_sites = [
            s
            for s in all_sites
            if (s.get("domain") or "").lower() == target
            or (s.get("id") or "").lower() == target
            or (s.get("api_key") or "").lower() == target
        ]

    sites_data = []
    for s in all_sites:
        s_domain = s.get("domain", "").lower()
        s_key = s.get("api_key", "").lower()
        s_id = s.get("id", "").lower()
        matching_sessions = [
            sum_item
            for sum_item in in_range
            if (sum_item.get("host") or "").lower() == s_domain
            or (sum_item.get("host") or "").lower().endswith("." + s_domain)
            or (sum_item.get("project_id") or "").lower() == s_key
            or (sum_item.get("project_id") or "").lower() == s_id
        ]
        active_now = sum(
            1 for sum_item in matching_sessions if now - float(sum_item.get("last_seen") or 0) < ACTIVE_WINDOW_SECONDS
        )
        last_seen_time = max(
            [float(sum_item.get("last_seen") or 0) for sum_item in matching_sessions],
            default=s.get("created_at", now),
        )
        site_entry = {
            "id": s.get("id"),
            "name": s.get("name"),
            "domain": s.get("domain"),
            "api_key": s.get("api_key"),
            "total_sessions": len(matching_sessions),
            "active_visitors": active_now,
            "last_seen": last_seen_time,
            "created_at": s.get("created_at"),
            "snippet": f'<script src="{config.sdk_url}" data-api-host="{config.site_url}" data-api-key="{s.get("api_key")}" defer></script>',
        }
        # Only expose secret key to the owner of this site or master admin
        if authorized_scope == "*" or (s.get("domain") or "").lower() == authorized_scope.lower():
            site_entry["secret_key"] = s.get("secret_key")

        sites_data.append(site_entry)

    paths_data = compute_paths(summaries)
    pages_data = compute_pages(with_events, vitals["pages"])
    funnel["is_custom"] = bool(query.funnel)
    funnel["suggested_funnels"] = compute_suggested_funnels(paths_data, pages_data, config)

    return {
        "status": 1,
        "generated_at": now,
        "current_site": query.site,
        "sites": sites_data,
        "scope": {
            "site": query.site,
            "hours": query.hours,
            "device_type": query.device_type,
            "browser": query.browser,
            "os": query.os,
            "status": query.status,
            "search": query.search,
            "is_admin": authorized_scope == "*",
            "authorized_site": None if authorized_scope == "*" else authorized_scope,
            "sessions_analyzed": len(summaries),
            "sessions_in_range": len(in_range),
            "sessions_stored": len(stored),
        },
        "setup": setup_info(store, config),
        "metrics": metrics,
        "comparison": comparison,
        "trend": compute_trend(with_events, query.hours, now, query.tz_offset),
        "sessions": [format_session(summary, now) for summary in visible[: query.limit]],
        "funnel": funnel,
        "paths": paths_data,
        "friction": compute_friction(with_events),
        "pages": pages_data,
        "web_vitals": vitals["site"],
        "referrers": compute_referrers(summaries),
        "errors": compute_errors(with_events),
        "activity": compute_activity(recent_events, summaries, now),
        # Realtime ignores the time range: it is always the last 30 minutes across every session.
        "realtime": compute_realtime(stored, recent_events, now),
        # Computed before the device, browser and OS scope so filter menus keep every option.
        "breakdowns": compute_breakdowns(in_range),
    }


def setup_info(store: SessionStore, config: HotspotConfig) -> dict[str, Any]:
    from products.hotspot import __version__  # noqa: PLC0415 — avoids a package import cycle at module load

    return {
        "version": __version__,
        "site_url": config.site_url,
        "sdk_url": config.sdk_url,
        "api_key": config.api_key_for_pages,
        "ingestion_keys_configured": bool(config.api_keys),
        "dashboard_protected": config.dashboard_protected,
        "demo_enabled": config.demo_enabled,
        "storage": store.backend_name,
        "conversion_events": sorted(config.conversion_events),
        "session_ttl_seconds": config.session_ttl_seconds,
        "replay_enabled": config.replay_enabled,
        "max_recording_bytes": config.max_recording_bytes,
    }


def _matches_scope(summary: Mapping[str, Any], query: OverviewQuery) -> bool:
    if query.site != "all":
        host = str(summary.get("host") or "").lower()
        if not host and summary.get("entry_url"):
            try:
                host = str(urlsplit(summary["entry_url"]).hostname or "").lower()
            except Exception:
                host = ""
        project_id = str(summary.get("project_id") or "").lower()
        wanted = query.site.lower()
        if wanted not in (host, project_id) and not host.endswith("." + wanted):
            return False
    for field, wanted in (("device_type", query.device_type), ("browser", query.browser), ("os", query.os)):
        if wanted != "all" and str(summary.get(field) or "").lower() != wanted:
            return False
    return True


def _matches_session_filter(summary: Mapping[str, Any], query: OverviewQuery, now: float) -> bool:
    status = session_status(summary, now)
    wanted = query.status
    if wanted == "identified":
        if not summary.get("identity"):
            return False
    elif wanted == "dead":
        if not summary.get("dead_click_count"):
            return False
    elif wanted == "broken":
        if not (summary.get("rage_click_count") or summary.get("dead_click_count") or summary.get("error_count")):
            return False
    elif wanted != "all" and status != wanted:
        return False
    if query.search:
        needle = query.search.lower()
        identity = summary.get("identity") or {}
        haystack = " ".join(
            str(value or "")
            for value in (
                summary.get("session_id"),
                summary.get("distinct_id"),
                identity.get("email"),
                identity.get("name"),
                identity.get("company"),
                summary.get("entry_page"),
                summary.get("referrer_host"),
                summary.get("country"),
                " ".join(summary.get("pages") or []),
            )
        ).lower()
        if needle not in haystack:
            return False
    return True


def _share(part: int, whole: int) -> float:
    return round(part / whole * 100, 1) if whole else 0.0
