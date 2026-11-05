"""Everything the dashboard shows, computed from stored session summaries and events."""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from products.hotspot.backend.config import HotspotConfig
from products.hotspot.backend.store import SessionStore

ACTIVE_WINDOW_SECONDS = 300
BOUNCE_SECONDS = 15
MAX_SESSIONS_ANALYZED = 1000
MAX_LIST_ROWS = 25
STATUS_FILTERS = frozenset({"all", "converted", "rage", "error", "bounced", "engaged", "active", "identified", "dead"})
STEP_KINDS = frozenset({"path", "event", "click", "text"})
CLICK_EVENTS = frozenset({"$autocapture", "$rageclick"})


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
    hours: int = 0
    limit: int = 200
    funnel: tuple[str, ...] = ()
    conversion_event: str | None = None

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
            hours=_bounded_int(params.get("hours"), default=0, low=0, high=24 * 90),
            limit=_bounded_int(params.get("limit"), default=200, low=1, high=MAX_SESSIONS_ANALYZED),
            funnel=tuple(step.strip() for step in funnel_raw.split("|") if step.strip())[:8],
            conversion_event=(params.get("conversion_event") or "").strip()[:200] or None,
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
    total = 0
    for _summary, events in sessions:
        total += 1
        index = 0
        previous_ts: float | None = None
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

    rows: list[dict[str, Any]] = []
    first = reached[0] if reached else 0
    for position, step in enumerate(steps):
        count = reached[position]
        previous = reached[position - 1] if position else count
        conv_from_prev = round(count / previous * 100, 1) if previous else 0.0
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
    for summary in summaries:
        pages = [page for page in (summary.get("pages") or []) if isinstance(page, str)]
        if not pages:
            continue
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


def compute_pages(
    sessions: Iterable[tuple[Mapping[str, Any], Sequence[Mapping[str, Any]]]],
) -> list[dict[str, Any]]:
    views: Counter[str] = Counter()
    sessions_by_page: dict[str, set[str]] = defaultdict(set)
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
        "ai_sessions": ai_sessions,
        "ai_sources": [{"source": source, "sessions": count} for source, count in ai_sources.most_common(10)],
    }


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


def compute_breakdowns(summaries: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for field in ("device_type", "browser", "os", "country"):
        counter: Counter[str] = Counter(
            str(summary.get(field)) for summary in summaries if summary.get(field) not in (None, "")
        )
        result[field] = dict(counter.most_common(20))
    return result


def build_overview(store: SessionStore, query: OverviewQuery, config: HotspotConfig, now: float) -> dict[str, Any]:
    stored = store.sessions(limit=MAX_SESSIONS_ANALYZED)
    in_range = stored
    if query.hours:
        cutoff = now - query.hours * 3600
        in_range = [summary for summary in stored if float(summary.get("last_seen") or 0) >= cutoff]
    summaries = [summary for summary in in_range if _matches_scope(summary, query)]

    with_events = [(summary, store.session_events(str(summary.get("session_id")))) for summary in summaries]
    visible = [summary for summary in summaries if _matches_session_filter(summary, query, now)]

    steps = parse_funnel_steps(query.funnel, config)
    if query.conversion_event:
        steps = [*steps[:-1], _step("event", query.conversion_event)] if query.funnel else steps
    funnel = compute_funnel(with_events, steps)

    total = len(summaries)
    converted = sum(1 for summary in summaries if summary.get("converted"))
    bounced = sum(1 for summary in summaries if session_status(summary, now) == "bounced")
    depths = [summary["max_scroll_depth"] for summary in summaries if summary.get("max_scroll_depth") is not None]
    metrics = {
        "sessions": total,
        "filtered_sessions": len(visible),
        "active_sessions": sum(1 for summary in summaries if now - float(summary.get("last_seen") or 0) < 300),
        "identified_sessions": sum(1 for summary in summaries if summary.get("identity")),
        "pageviews": sum(int(summary.get("pageview_count") or 0) for summary in summaries),
        "clicks": sum(int(summary.get("click_count") or 0) for summary in summaries),
        "rage_clicks": sum(int(summary.get("rage_click_count") or 0) for summary in summaries),
        "dead_clicks": sum(int(summary.get("dead_click_count") or 0) for summary in summaries),
        "form_submits": sum(int(summary.get("form_submit_count") or 0) for summary in summaries),
        "errors": sum(int(summary.get("error_count") or 0) for summary in summaries),
        "converted_sessions": converted,
        "conversion_rate": _share(converted, total),
        "bounce_rate": _share(bounced, total),
        "avg_duration_seconds": round(
            statistics.mean(int(summary.get("duration_seconds") or 0) for summary in summaries), 1
        )
        if summaries
        else 0.0,
        "avg_scroll_depth": round(statistics.mean(depths)) if depths else None,
        "ai_referral_sessions": sum(1 for summary in summaries if summary.get("is_ai_referral")),
    }

    return {
        "status": 1,
        "generated_at": now,
        "scope": {
            "hours": query.hours,
            "device_type": query.device_type,
            "browser": query.browser,
            "os": query.os,
            "status": query.status,
            "search": query.search,
            "sessions_analyzed": total,
            "sessions_in_range": len(in_range),
            "sessions_stored": len(stored),
        },
        "setup": setup_info(store, config),
        "metrics": metrics,
        "sessions": [format_session(summary, now) for summary in visible[: query.limit]],
        "funnel": funnel,
        "paths": compute_paths(summaries),
        "friction": compute_friction(with_events),
        "pages": compute_pages(with_events),
        "referrers": compute_referrers(summaries),
        "errors": compute_errors(with_events),
        "activity": compute_activity(store.recent_events(limit=config.max_events), summaries, now),
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
    }


def _matches_scope(summary: Mapping[str, Any], query: OverviewQuery) -> bool:
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
