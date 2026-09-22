#!/usr/bin/env python3
"""
Netwroxia — Phase E3: Beacons Section

Per-router beacon monitoring: RTT vs baseline, consecutive misses,
source of baseline, last-seen timestamps. Reads through E1.

Usage:
    from dashboard.sections import beacons
    beacons.render()
    beacons.render(artifacts={...})
"""

import sys
from datetime import datetime, timezone
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
def _fmt_float(value: Any, decimals: int = 3, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.{decimals}f}{suffix}"
    except (TypeError, ValueError):
        return str(value)


def _fmt_int(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return str(value)


def _get(artifacts: Dict[str, Any], name: str) -> Dict[str, Any]:
    data = artifacts.get(name)
    if not isinstance(data, dict):
        return {}
    if data.get("_missing") or data.get("_error"):
        return {}
    return data


def _status_emoji(status: str) -> str:
    return {
        "OK": "🟢",
        "DEGRADED": "🟡",
        "CRITICAL": "🔴",
        "MISSING": "⚫",
    }.get(status, "⚪")


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


# ── PANEL: SUMMARY ──────────────────────────────────────────────────────────
def render_summary(artifacts: Dict[str, Any]) -> None:
    beacon_health = _get(artifacts, "beacon_health")
    if not beacon_health:
        st.info("Beacon health not available. "
                "Run: `python3 beacons/beacon_collector.py`")
        return

    summary = beacon_health.get("network_summary", {})
    cols = st.columns(6)
    cols[0].metric("Score", _fmt_int(summary.get("beacon_health_score")))
    cols[1].metric("Total", _fmt_int(summary.get("total_routers")))
    cols[2].metric("OK", _fmt_int(summary.get("ok")))
    cols[3].metric("Degraded", _fmt_int(summary.get("degraded")))
    cols[4].metric("Critical", _fmt_int(summary.get("critical")))
    cols[5].metric("Missing", _fmt_int(summary.get("missing")))

    ts = beacon_health.get("timestamp")
    if ts:
        st.caption(f"Updated: {ts}  ·  ({_age_str(ts)})")


# ── PANEL: STATUS BREAKDOWN ─────────────────────────────────────────────────
def render_status_chart(artifacts: Dict[str, Any]) -> None:
    st.markdown("#### Status by Router")
    beacon_health = _get(artifacts, "beacon_health")
    if not beacon_health:
        return

    routers = beacon_health.get("routers", {})
    if not routers:
        st.caption("No routers in beacon health.")
        return

    for name, entry in routers.items():
        status = entry.get("status", "UNKNOWN")
        score = int(entry.get("score", 0))
        emoji = _status_emoji(status)
        st.markdown(f"{emoji} **{name}** — {status} (score {score})")
        st.progress(min(max(score, 0), 100) / 100.0)


# ── PANEL: MISSES TABLE ─────────────────────────────────────────────────────
def render_misses(artifacts: Dict[str, Any]) -> None:
    st.markdown("#### Consecutive Misses")
    beacon_health = _get(artifacts, "beacon_health")
    if not beacon_health:
        return

    routers = beacon_health.get("routers", {})
    if not routers:
        st.caption("No data.")
        return

    rows = []
    for name, entry in routers.items():
        rows.append({
            "Router": name,
            "Status": entry.get("status", "?"),
            "Misses": entry.get("consecutive_misses", 0),
            "Seq": entry.get("seq", 0),
        })

    try:
        import pandas as pd  # noqa
        st.dataframe(rows, use_container_width=True, hide_index=True)
    except Exception:
        for r in rows:
            st.markdown(f"- `{r['Router']}` — misses={r['Misses']}")


# ── PANEL: PER-ROUTER DETAILS ───────────────────────────────────────────────
def render_router_details(artifacts: Dict[str, Any]) -> None:
    st.markdown("#### Per-Router Detail")
    beacon_health = _get(artifacts, "beacon_health")
    baselines = _get(artifacts, "baselines")

    if not beacon_health:
        return

    routers = beacon_health.get("routers", {})
    baseline_map = baselines.get("routers", {}) if baselines else {}

    if not routers:
        st.caption("No beacon entries.")
        return

    for name, entry in routers.items():
        status = entry.get("status", "UNKNOWN")
        emoji = _status_emoji(status)

        with st.expander(
            f"{emoji} {name} — {status} "
            f"({_fmt_float(entry.get('rtt_ms'), 3)} ms)",
            expanded=False,
        ):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("RTT", _fmt_float(entry.get("rtt_ms"), 3, " ms"))
            c2.metric("Min RTT", _fmt_float(entry.get("rtt_min_ms"), 3, " ms"))
            c3.metric("Max RTT", _fmt_float(entry.get("rtt_max_ms"), 3, " ms"))
            c4.metric("Loss", _fmt_float(entry.get("loss_pct"), 2, "%"))

            c5, c6, c7, c8 = st.columns(4)
            c5.metric("Baseline", _fmt_float(entry.get("baseline_rtt_ms"), 3, " ms"))
            c6.metric("Deviation", _fmt_float(entry.get("deviation_pct"), 1, "%"))
            c7.metric("Z-score", _fmt_float(entry.get("z_score"), 3))
            c8.metric("Misses", _fmt_int(entry.get("consecutive_misses")))

            b_entry = baseline_map.get(name, {})
            st.caption(
                f"Baseline source: `{entry.get('baseline_source', b_entry.get('source', '?'))}`  ·  "
                f"std_rtt=`{_fmt_float(entry.get('std_rtt_ms') or b_entry.get('std_rtt_ms'), 4)}` ms  ·  "
                f"seq=`{entry.get('seq', '?')}`  ·  "
                f"last_seen: {entry.get('last_seen', 'n/a')} ({_age_str(entry.get('last_seen'))})"
            )


# ── PANEL: RAW STATE ────────────────────────────────────────────────────────
def render_raw_state(artifacts: Dict[str, Any]) -> None:
    with st.expander("🔍 Raw beacon state", expanded=False):
        state = _get(artifacts, "beacon_state")
        if not state:
            st.caption("Beacon state not available.")
            return
        st.json(state)


# ── PUBLIC ENTRYPOINT ───────────────────────────────────────────────────────
def render(artifacts: Optional[Dict[str, Any]] = None) -> None:
    if artifacts is None:
        artifacts = al.load_all()

    st.subheader("📡 Beacon Monitoring")
    render_summary(artifacts)
    st.markdown("---")

    col_l, col_r = st.columns(2)
    with col_l:
        render_status_chart(artifacts)
    with col_r:
        render_misses(artifacts)

    st.markdown("---")
    render_router_details(artifacts)

    st.markdown("---")
    render_raw_state(artifacts)
