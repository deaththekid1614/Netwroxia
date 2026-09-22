#!/usr/bin/env python3
"""
Netwroxia — Phase E10: Audit Section

Audit trail viewer. Reads C1's audit logs directly (JSONL, one event
per line, one file per day). Filters by prefix, router, or incident_id.

Usage:
    from dashboard.sections import audit
    audit.render()
    audit.render(artifacts={...})   # artifacts param ignored here
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st

# ── PATH SETUP ──────────────────────────────────────────────────────────────
_THIS = Path(__file__).resolve()
_PROJECT_ROOT = _THIS.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ── CONFIG ──────────────────────────────────────────────────────────────────
AUDIT_DIR = _PROJECT_ROOT / "audit" / "audit_logs"

# Ordered for readability
PREFIXES = (
    "orchestrator.",
    "execution.",
    "action.",
    "incident.",
    "approval.",
    "snapshot.",
)


# ── HELPERS ─────────────────────────────────────────────────────────────────
def _age_str(iso_ts: Optional[str]) -> str:
    if not iso_ts:
        return "n/a"
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return "n/a"
    delta = (datetime.now(timezone.utc) - dt).total_seconds()
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta/60)}m ago"
    if delta < 86400:
        return f"{int(delta/3600)}h ago"
    return f"{int(delta/86400)}d ago"


def _list_log_files() -> List[Path]:
    if not AUDIT_DIR.exists():
        return []
    return sorted(AUDIT_DIR.glob("*.jsonl"), reverse=True)


def _read_all_events(limit: int = 5000) -> List[Dict[str, Any]]:
    """
    Read every event from every JSONL file, oldest file first.
    Bounded by `limit` to keep it snappy under auto-refresh.
    """
    files = sorted(_list_log_files())  # oldest → newest
    out: List[Dict[str, Any]] = []
    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
                    if len(out) >= limit:
                        return out
        except OSError:
            continue
    return out


def _count_by_prefix(events: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {p: 0 for p in PREFIXES}
    counts["other"] = 0
    for e in events:
        name = e.get("event", "")
        matched = False
        for p in PREFIXES:
            if name.startswith(p):
                counts[p] += 1
                matched = True
                break
        if not matched:
            counts["other"] += 1
    return counts


def _filter_events(
    events: List[Dict[str, Any]],
    prefix: Optional[str] = None,
    router: Optional[str] = None,
    incident_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    out = []
    for e in events:
        name = e.get("event", "")
        if prefix and not name.startswith(prefix):
            continue
        if router and e.get("router") != router:
            continue
        if incident_id and e.get("incident_id") != incident_id:
            continue
        out.append(e)
    return out


# ── PANEL: SUMMARY ──────────────────────────────────────────────────────────
def render_summary(events: List[Dict[str, Any]]) -> None:
    counts = _count_by_prefix(events)
    total = sum(counts.values())

    cols = st.columns(7)
    cols[0].metric("Total events", total)
    cols[1].metric("orchestrator", counts.get("orchestrator.", 0))
    cols[2].metric("execution", counts.get("execution.", 0))
    cols[3].metric("action", counts.get("action.", 0))
    cols[4].metric("incident", counts.get("incident.", 0))
    cols[5].metric("approval", counts.get("approval.", 0))
    cols[6].metric("snapshot", counts.get("snapshot.", 0))

    files = _list_log_files()
    if files:
        st.caption(
            f"Files on disk: {len(files)}  ·  "
            f"latest: `{files[0].name}`"
        )


# ── PANEL: FILTERS ──────────────────────────────────────────────────────────
def render_filters(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return the current filter state."""
    c1, c2, c3, c4 = st.columns(4)

    prefix_choices = ["(all)"] + list(PREFIXES) + ["(other)"]
    prefix_sel = c1.selectbox("Event prefix", prefix_choices, index=0)
    prefix = None
    if prefix_sel == "(other)":
        # "other" = anything not matching a known prefix
        prefix = "__other__"
    elif prefix_sel != "(all)":
        prefix = prefix_sel

    routers = sorted({e.get("router") for e in events if e.get("router")})
    router_sel = c2.selectbox("Router", ["(all)"] + routers, index=0)
    router = None if router_sel == "(all)" else router_sel

    incidents = sorted({e.get("incident_id") for e in events
                        if e.get("incident_id")})
    inc_sel = c3.selectbox("Incident", ["(all)"] + incidents, index=0)
    incident_id = None if inc_sel == "(all)" else inc_sel

    limit = c4.slider("Max rows", min_value=20, max_value=500,
                      value=100, step=20)

    return {
        "prefix": prefix,
        "router": router,
        "incident_id": incident_id,
        "limit": limit,
    }


def _apply_other_prefix(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep events that do NOT start with any known prefix."""
    out = []
    for e in events:
        name = e.get("event", "")
        if not any(name.startswith(p) for p in PREFIXES):
            out.append(e)
    return out


# ── PANEL: EVENTS TABLE ─────────────────────────────────────────────────────
def render_events(events: List[Dict[str, Any]], filters: Dict[str, Any]) -> None:
    st.markdown("#### Recent Events")

    prefix = filters["prefix"]
    if prefix == "__other__":
        filtered = _apply_other_prefix(events)
    else:
        filtered = _filter_events(
            events,
            prefix=prefix,
            router=filters["router"],
            incident_id=filters["incident_id"],
        )

    if not filtered:
        st.caption("No events match the current filters.")
        return

    # newest first, cap at limit
    filtered = list(reversed(filtered))[: filters["limit"]]

    rows = []
    for e in filtered:
        rows.append({
            "When": _age_str(e.get("ts")),
            "Ts": (e.get("ts") or "")[:19],
            "Event": e.get("event", "?"),
            "Incident": (e.get("incident_id") or "-")[:16],
            "Router": e.get("router") or "-",
            "Actor": e.get("actor") or "-",
            "Data": _short_data(e.get("data")),
        })

    try:
        import pandas as pd  # noqa
        st.dataframe(rows, use_container_width=True, hide_index=True)
    except Exception:
        for r in rows:
            st.markdown(f"- `{r['Ts']}` **{r['Event']}** "
                        f"({r['Router']}, {r['Actor']})")

    st.caption(f"Showing {len(rows)} of {len(filtered)} matching events.")


def _short_data(data: Any) -> str:
    if not data:
        return ""
    try:
        s = json.dumps(data, default=str)
    except (TypeError, ValueError):
        s = str(data)
    return s[:60] + ("…" if len(s) > 60 else "")


# ── PANEL: FILE DRILL-DOWN ──────────────────────────────────────────────────
def render_files() -> None:
    files = _list_log_files()
    if not files:
        return

    with st.expander("📁 Raw log files", expanded=False):
        st.caption(f"{len(files)} file(s) on disk")
        for path in files[:10]:
            try:
                size = path.stat().st_size
                lines = sum(1 for _ in open(path))
            except OSError:
                size, lines = 0, 0
            st.markdown(
                f"- `{path.name}` — {size}B  ·  {lines} events"
            )


# ── PUBLIC ENTRYPOINT ───────────────────────────────────────────────────────
def render(artifacts: Optional[Dict[str, Any]] = None) -> None:
    # artifacts param accepted for symmetry with other sections; ignored.
    st.subheader("📋 Audit Trail")

    events = _read_all_events(limit=5000)
    if not events:
        st.info("No audit events found. "
                "The log at `audit/audit_logs/` will populate as the "
                "pipeline runs.")
        return

    render_summary(events)
    st.markdown("---")
    filters = render_filters(events)
    render_events(events, filters)
    st.markdown("---")
    render_files()
