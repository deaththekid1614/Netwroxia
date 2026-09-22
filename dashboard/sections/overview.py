#!/usr/bin/env python3
"""
Netwroxia — Phase E2: Overview Section

Executive summary of the current state. Reads through E1's artifact
loader. No direct file I/O — everything comes via the artifacts dict.

Usage (from app_v2.py):
    from dashboard.sections import overview
    overview.render()

Or with injected data (for tests):
    overview.render(artifacts={"health": {...}, ...})
"""

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st

# ── PATH SETUP ──────────────────────────────────────────────────────────────
_THIS = Path(__file__).resolve()
_PROJECT_ROOT = _THIS.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dashboard.utils import artifact_loader as al  # noqa: E402


# ── HELPERS ─────────────────────────────────────────────────────────────────
def _fmt_value(value: Any, suffix: str = "") -> str:
    """Format a numeric value for st.metric, or 'n/a' if None/missing."""
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.2f}{suffix}"
    return f"{value}{suffix}"


def _is_available(artifacts: Dict[str, Any], name: str) -> bool:
    """True iff key exists, value is a dict, and no _missing/_error flag."""
    if name not in artifacts:
        return False
    data = artifacts[name]
    if not isinstance(data, dict):
        return False
    return not (data.get("_missing") or data.get("_error"))


def _get(artifacts: Dict[str, Any], name: str) -> Dict[str, Any]:
    data = artifacts.get(name) or {}
    if data.get("_missing") or data.get("_error"):
        return {}
    return data


# ── PANEL: EXECUTIVE METRICS ────────────────────────────────────────────────
def render_executive(artifacts: Dict[str, Any]) -> None:
    health = _get(artifacts, "health")
    beacon_health = _get(artifacts, "beacon_health")
    daily = _get(artifacts, "daily_report")
    biz = _get(artifacts, "business_impact")

    # Network score
    net = health.get("network", {}) if health else {}
    score = net.get("score")
    status = net.get("status", "")

    # Beacons OK
    if beacon_health:
        summary = beacon_health.get("network_summary", {})
        b_ok = summary.get("ok", 0)
        b_total = summary.get("total_routers", 0)
        beacon_display = f"{b_ok}/{b_total}" if b_total else "n/a"
    else:
        beacon_display = "n/a"

    # Incidents today
    inc_today: Optional[int] = None
    critical_today: Optional[int] = None
    if daily:
        inc_today = daily.get("incidents_today", {}).get("incidents_total")
        summary = daily.get("summary", {})
        critical_today = summary.get("critical_count")

    # Loss rate
    loss_per_min = "n/a"
    if biz:
        loss_per_min = biz.get("current_loss", {}).get("per_minute_display", "n/a")

    cols = st.columns(5)
    cols[0].metric("Network Score", _fmt_value(score), status or None)
    cols[1].metric("Beacons OK", beacon_display)
    cols[2].metric("Incidents Today", _fmt_value(inc_today))
    cols[3].metric("Loss Rate", loss_per_min)
    cols[4].metric("Critical (today)", _fmt_value(critical_today))


# ── PANEL: HEALTH BREAKDOWN ─────────────────────────────────────────────────
def render_health_breakdown(artifacts: Dict[str, Any]) -> None:
    st.markdown("### 💚 Network Health")

    health = _get(artifacts, "health")
    if not health:
        st.info("Health artifact not available. "
                "Run: `python3 analytics/health_score.py`")
        return

    net = health.get("network", {})
    st.markdown(
        f"**Overall:** `{net.get('score', 'n/a')}`  "
        f"({net.get('status', 'UNKNOWN')})  ·  "
        f"routers={net.get('router_count', '?')}"
    )

    routers = health.get("routers", {})
    if not routers:
        st.caption("No router entries in health artifact.")
        return

    # Group by status
    by_status: Dict[str, List[str]] = {}
    for name, entry in routers.items():
        s = (entry.get("status") or "UNKNOWN").upper()
        by_status.setdefault(s, []).append(name)

    order = ["HEALTHY", "WARNING", "CRITICAL", "DOWN", "UNKNOWN"]
    for status in order:
        names = by_status.get(status)
        if not names:
            continue
        emoji = {
            "HEALTHY": "🟢",
            "WARNING": "🟡",
            "CRITICAL": "🔴",
            "DOWN": "⚫",
            "UNKNOWN": "⚪",
        }.get(status, "⚪")
        st.markdown(f"{emoji} **{status}** — {', '.join(names)}")


# ── PANEL: BEACON STATUS ────────────────────────────────────────────────────
def render_beacon_panel(artifacts: Dict[str, Any]) -> None:
    st.markdown("### 📡 Beacon Status")

    beacon_health = _get(artifacts, "beacon_health")
    baselines = _get(artifacts, "baselines")

    if not beacon_health:
        st.info("Beacon health not available. "
                "Run: `python3 beacons/beacon_collector.py`")
        return

    summary = beacon_health.get("network_summary", {})
    st.markdown(
        f"**Score:** `{summary.get('beacon_health_score', 'n/a')}` / 100  ·  "
        f"routers OK: {summary.get('ok', 0)} / {summary.get('total_routers', 0)}"
    )

    routers = beacon_health.get("routers", {})
    baseline_map = baselines.get("routers", {}) if baselines else {}

    if not routers:
        st.caption("No beacon entries.")
        return

    for name, entry in routers.items():
        status = entry.get("status", "UNKNOWN")
        rtt = entry.get("rtt_ms", 0)
        baseline_rtt = entry.get("baseline_rtt_ms", 0)
        dev = entry.get("deviation_pct", 0)
        misses = entry.get("consecutive_misses", 0)

        emoji = {
            "OK": "🟢",
            "DEGRADED": "🟡",
            "CRITICAL": "🔴",
            "MISSING": "⚫",
        }.get(status, "⚪")

        src = baseline_map.get(name, {}).get("source", "?")
        st.markdown(
            f"{emoji} **{name}** — rtt `{rtt:.3f}ms`  "
            f"(base `{baseline_rtt:.3f}ms`, dev `{dev:+.1f}%`, "
            f"src `{src}`)  ·  misses={misses}"
        )


# ── PANEL: INCIDENTS TODAY ──────────────────────────────────────────────────
def render_incidents(artifacts: Dict[str, Any]) -> None:
    st.markdown("### 🚨 Incidents (today)")

    daily = _get(artifacts, "daily_report")
    if not daily:
        st.info("Daily report not available. "
                "Run: `python3 reports/daily_report.py generate`")
        return

    inc = daily.get("incidents_today", {})
    sev = inc.get("by_severity", {})

    cols = st.columns(5)
    cols[0].metric("Total", _fmt_value(inc.get("incidents_total", 0)))
    cols[1].metric("Critical", _fmt_value(sev.get("CRITICAL", 0)))
    cols[2].metric("High", _fmt_value(sev.get("HIGH", 0)))
    cols[3].metric("Medium", _fmt_value(sev.get("MEDIUM", 0)))
    cols[4].metric("Low", _fmt_value(sev.get("LOW", 0)))

    routers = inc.get("unique_routers") or []
    downtime = inc.get("downtime_weighted_seconds", 0)
    trend = inc.get("trend_label", "n/a")
    st.caption(
        f"Routers affected: {', '.join(routers) if routers else '—'}  ·  "
        f"Weighted downtime: {downtime:.0f}s  ·  "
        f"Trend: {trend}"
    )


# ── PANEL: DATA SOURCES ─────────────────────────────────────────────────────
def render_data_sources(artifacts: Dict[str, Any]) -> None:
    with st.expander("🔌 Data source health", expanded=False):
        rows = []
        for name in sorted(artifacts.keys()):
            data = artifacts[name] or {}
            if data.get("_missing"):
                state = "MISSING"
            elif data.get("_error"):
                state = f"ERROR: {str(data['_error'])[:40]}"
            else:
                state = "OK"
            rows.append({"artifact": name, "state": state})
        try:
            import pandas as pd  # noqa
            st.dataframe(rows, use_container_width=True, hide_index=True)
        except Exception:
            # Fallback: plain markdown table
            for r in rows:
                st.markdown(f"- `{r['artifact']}` — {r['state']}")


# ── PUBLIC ENTRYPOINT ───────────────────────────────────────────────────────
def render(artifacts: Optional[Dict[str, Any]] = None) -> None:
    """
    Render the Overview section. If artifacts is None, load via E1.
    """
    if artifacts is None:
        artifacts = al.load_all()

    st.subheader("🏠 Network Overview")

    render_executive(artifacts)
    st.markdown("---")

    col_l, col_r = st.columns(2)
    with col_l:
        render_health_breakdown(artifacts)
    with col_r:
        render_beacon_panel(artifacts)

    st.markdown("---")
    render_incidents(artifacts)

    st.markdown("---")
    render_data_sources(artifacts)
