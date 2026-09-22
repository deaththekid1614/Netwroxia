#!/usr/bin/env python3
"""
Netwroxia — Phase E9: Reports Section

Daily, weekly, and per-incident report viewer. Reads daily/weekly
through E1's artifact loader. Reads per-incident files directly from
reports/incidents/.

Usage:
    from dashboard.sections import reports
    reports.render()
    reports.render(artifacts={...})
"""

import json
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

INCIDENTS_DIR = _PROJECT_ROOT / "reports" / "incidents"


# ── HELPERS ─────────────────────────────────────────────────────────────────
def _get(artifacts: Dict[str, Any], name: str) -> Dict[str, Any]:
    data = artifacts.get(name)
    if not isinstance(data, dict):
        return {}
    if data.get("_missing") or data.get("_error"):
        return {}
    return data


def _fmt(value: Any, decimals: int = 2) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.{decimals}f}"
    except (TypeError, ValueError):
        return str(value)


def _list_incident_reports() -> List[Path]:
    if not INCIDENTS_DIR.exists():
        return []
    return sorted(INCIDENTS_DIR.glob("*.json"), reverse=True)


def _load_incident_report(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


# ── PANEL: DAILY ────────────────────────────────────────────────────────────
def render_daily(artifacts: Dict[str, Any]) -> None:
    st.markdown("#### 📅 Daily Report")
    daily = _get(artifacts, "daily_report")

    if not daily:
        st.info("Daily report not available. "
                "Run: `python3 reports/daily_report.py generate`")
        return

    st.markdown(
        f"**Date:** `{daily.get('report_date', '?')}`  ·  "
        f"generated: `{daily.get('generated_at', '?')}`"
    )

    ex = daily.get("executive_summary", {})
    cols = st.columns(6)
    cols[0].metric("Network score", _fmt(ex.get("network_score"), 1))
    cols[1].metric("Status", ex.get("network_status", "?"))
    cols[2].metric("SLA compliance",
                   f"{_fmt(ex.get('sla_compliance_rate'), 1)}%")
    cols[3].metric("MTTD", f"{_fmt(ex.get('mttd_min'), 1)} min")
    cols[4].metric("MTTR", f"{_fmt(ex.get('mttr_min'), 1)} min")
    cols[5].metric("Availability",
                   f"{_fmt(ex.get('availability_pct'), 2)}%")

    inc = daily.get("incidents_today", {})
    sev = inc.get("by_severity", {})
    cols2 = st.columns(5)
    cols2[0].metric("Incidents", inc.get("incidents_total", 0))
    cols2[1].metric("Critical", sev.get("CRITICAL", 0))
    cols2[2].metric("High", sev.get("HIGH", 0))
    cols2[3].metric("Medium", sev.get("MEDIUM", 0))
    cols2[4].metric("Low", sev.get("LOW", 0))

    routers = inc.get("unique_routers") or []
    if routers:
        st.caption(f"Routers affected: {', '.join(routers)}")


# ── PANEL: WEEKLY ───────────────────────────────────────────────────────────
def render_weekly(artifacts: Dict[str, Any]) -> None:
    st.markdown("#### 📆 Weekly Report")
    weekly = _get(artifacts, "weekly_report")

    if not weekly:
        st.info("Weekly report not available. "
                "Run: `python3 reports/weekly_report.py generate`")
        return

    st.markdown(
        f"**Week:** `{weekly.get('week_label', '?')}`  ·  "
        f"{weekly.get('week_start', '?')} → {weekly.get('week_end', '?')}  ·  "
        f"days_included=`{len(weekly.get('days_included', []))}/7`"
    )

    s = weekly.get("summary", {})
    cols = st.columns(5)
    cols[0].metric("Incidents", s.get("incidents_total", 0))
    cols[1].metric("Avg score", _fmt(s.get("avg_network_score"), 1))
    cols[2].metric("Min / Max",
                   f"{_fmt(s.get('min_network_score'), 1)} / "
                   f"{_fmt(s.get('max_network_score'), 1)}")
    cols[3].metric("Avg SLA", f"{_fmt(s.get('avg_sla_compliance_rate'), 1)}%")
    cols[4].metric("Downtime (w)", f"{_fmt(s.get('downtime_weighted_seconds_total'), 0)}s")

    # Per-day breakdown
    per_day = weekly.get("per_day", {})
    if per_day:
        st.markdown("**Per-day**")
        rows = []
        for day, v in sorted(per_day.items()):
            rows.append({
                "Date": day,
                "Incidents": v.get("incidents", 0),
                "Score": _fmt(v.get("network_score"), 1),
                "SLA %": _fmt(v.get("sla_compliance_rate"), 2),
            })
        try:
            import pandas as pd  # noqa
            st.dataframe(rows, use_container_width=True, hide_index=True)
        except Exception:
            for r in rows:
                st.markdown(f"- `{r['Date']}` — {r['Incidents']} incidents")

    # Comparison
    cmp = weekly.get("comparison_to_previous_week")
    if cmp:
        trend = cmp.get("trend_label", "?")
        emoji = {"improving": "🟢", "worsening": "🔴",
                 "stable": "🟡"}.get(trend, "⚪")
        st.markdown(
            f"**{emoji} vs {cmp.get('prev_week_label', '?')}**  ·  "
            f"Δincidents=`{cmp.get('delta_incidents')}`  ·  "
            f"Δscore=`{_fmt(cmp.get('delta_avg_network_score'), 1)}`  ·  "
            f"trend=`{trend}`"
        )


# ── PANEL: INCIDENT REPORTS ─────────────────────────────────────────────────
def render_incident_reports(artifacts: Dict[str, Any]) -> None:
    st.markdown("#### 📋 Incident Reports")

    paths = _list_incident_reports()
    if not paths:
        st.caption(
            "No per-incident reports yet. "
            "Run: `python3 reports/incident_report.py latest`"
        )
        return

    # Selector
    options = [p.name for p in paths]
    selected = st.selectbox("Select incident report", options, index=0)
    path = INCIDENTS_DIR / selected
    report = _load_incident_report(path)
    if report is None:
        st.warning(f"Could not parse {selected}")
        return

    s = report.get("summary", {})
    cols = st.columns(4)
    cols[0].metric("State", s.get("state", "?"))
    cols[1].metric("Severity", s.get("severity", "?"))
    cols[2].metric("Root", s.get("root_router", "?"))
    cols[3].metric("Duration", f"{_fmt(s.get('duration_seconds'), 1)}s")

    with st.expander("Timeline", expanded=False):
        timeline = report.get("timeline", [])
        for item in timeline:
            kind = item.get("kind", "?")
            ts = item.get("ts", "?")
            text = item.get("text", "")
            actor = item.get("actor", "-")
            st.markdown(f"- `{ts}` **[{kind.upper()}]** {text} — *{actor}*")

    with st.expander("RCA snapshot", expanded=False):
        rca = report.get("rca_snapshot")
        if rca:
            st.json(rca)
        else:
            st.caption("No RCA snapshot in this report.")


# ── PANEL: DATA SOURCE STATUS ──────────────────────────────────────────────
def render_sources_status(artifacts: Dict[str, Any]) -> None:
    with st.expander("🔍 Data source status", expanded=False):
        for key in ("daily_report", "weekly_report"):
            data = artifacts.get(key) or {}
            if data.get("_missing"):
                state = "MISSING"
            elif data.get("_error"):
                state = "ERROR"
            else:
                state = "OK"
            st.markdown(f"- `{key}` — **{state}**")
        st.markdown(
            f"- incident reports on disk: "
            f"**{len(_list_incident_reports())}**"
        )


# ── PUBLIC ENTRYPOINT ───────────────────────────────────────────────────────
def render(artifacts: Optional[Dict[str, Any]] = None) -> None:
    if artifacts is None:
        artifacts = al.load_all()

    st.subheader("📄 Reports")
    render_daily(artifacts)
    st.markdown("---")
    render_weekly(artifacts)
    st.markdown("---")
    render_incident_reports(artifacts)
    st.markdown("---")
    render_sources_status(artifacts)
