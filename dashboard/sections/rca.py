#!/usr/bin/env python3
"""
Netwroxia — Phase E6: RCA Section

Root cause analysis view. Shows incidents grouped by root,
causal graph (edges + confidence), and historical matches.

Reads through E1's artifact loader.

Usage:
    from dashboard.sections import rca
    rca.render()
    rca.render(artifacts={...})
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
def _get(artifacts: Dict[str, Any], name: str) -> Dict[str, Any]:
    data = artifacts.get(name)
    if not isinstance(data, dict):
        return {}
    if data.get("_missing") or data.get("_error"):
        return {}
    return data


def _severity_emoji(sev: str) -> str:
    return {
        "NONE": "🟢",
        "LOW": "🟢",
        "MEDIUM": "🟡",
        "HIGH": "🟠",
        "CRITICAL": "🔴",
    }.get((sev or "").upper(), "⚪")


def _fmt(value: Any, decimals: int = 3) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.{decimals}f}"
    except (TypeError, ValueError):
        return str(value)


# ── PANEL: SUMMARY ──────────────────────────────────────────────────────────
def render_summary(artifacts: Dict[str, Any]) -> None:
    correlation = _get(artifacts, "correlation")
    if not correlation:
        st.info("Correlation artifact not available. "
                "Run: `python3 rca/correlation_engine.py`")
        return

    summary = correlation.get("summary", {})
    sev = summary.get("severity_counts", {})

    cols = st.columns(6)
    cols[0].metric("Incidents", summary.get("incident_count", 0))
    cols[1].metric("Critical", sev.get("CRITICAL", 0))
    cols[2].metric("High", sev.get("HIGH", 0))
    cols[3].metric("Affected routers",
                   summary.get("total_affected_routers", 0))
    cols[4].metric("Users", summary.get("total_users_affected", 0))
    cols[5].metric("Top root", summary.get("top_root") or "-")

    if summary.get("has_critical_incident"):
        st.warning("Critical incident detected in this snapshot.")


# ── PANEL: INCIDENT CARDS ───────────────────────────────────────────────────
def _find_graph(graphs: List[Dict[str, Any]], incident_id: str) -> Optional[Dict[str, Any]]:
    for g in graphs:
        if g.get("graph_id") == incident_id or g.get("incident_id") == incident_id:
            return g
    return None


def _find_match(matches: List[Dict[str, Any]], incident_id: str) -> Optional[Dict[str, Any]]:
    for m in matches:
        if m.get("current_incident_id") == incident_id:
            return m
    return None


def render_incidents(artifacts: Dict[str, Any]) -> None:
    st.markdown("#### Correlated Incidents")

    correlation = _get(artifacts, "correlation")
    incidents = correlation.get("incidents", []) if correlation else []
    causal = _get(artifacts, "causal_graph").get("graphs", [])
    hist = _get(artifacts, "historical_match").get("matches", [])

    if not incidents:
        st.caption("No correlated incidents.")
        return

    for inc in incidents:
        iid = inc.get("incident_id", "?")
        severity = inc.get("severity", "UNKNOWN")
        emoji = _severity_emoji(severity)
        root = inc.get("root", "?")
        downstream_count = inc.get("downstream_count", 0)
        users = inc.get("total_users_affected", 0)

        header = (
            f"{emoji} {iid} — {severity}  ·  "
            f"root={root}  ·  downstream={downstream_count}  ·  "
            f"users={users}"
        )

        with st.expander(header, expanded=False):
            # Correlation details
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Root", root)
            c2.metric("Role", inc.get("root_role", "?"))
            c3.metric("Chain length", inc.get("affected_routers_count", 0))
            c4.metric("Score", _fmt(inc.get("severity_score"), 1))

            chain = inc.get("affected_routers", [])
            if chain:
                st.markdown(f"**Chain:** {' → '.join(chain)}")

            statuses = inc.get("statuses", [])
            if statuses:
                st.caption(f"Statuses: {', '.join(statuses)}")

            # Causal graph
            g = _find_graph(causal, iid)
            if g:
                st.markdown("**Causal graph**")
                rc = g.get("root_cause", {})
                st.caption(
                    f"confidence=`{_fmt(rc.get('confidence'), 3)}`  ·  "
                    f"depth=`{rc.get('propagation_depth', '?')}`  ·  "
                    f"affected=`{rc.get('affected_count', '?')}`"
                )
                edges = g.get("edges", [])
                if edges:
                    for e in edges:
                        marker = "→" if e.get("status") == "active" else "⇢"
                        st.markdown(
                            f"  `{e.get('source')}` {marker} "
                            f"`{e.get('target')}` "
                            f"({e.get('status', '?')})"
                        )

            # Historical match
            m = _find_match(hist, iid)
            if m and m.get("matches"):
                st.markdown("**Historical matches**")
                for i, hit in enumerate(m["matches"][:3], 1):
                    score = _fmt(hit.get("match_score"), 4)
                    past_id = hit.get("past_incident_id", "?")
                    past_title = hit.get("past_title", "")
                    recovery = hit.get("past_recovery_time_min", "?")
                    st.markdown(
                        f"  #{i}  `{past_id}` — score `{score}`  ·  "
                        f"recovery=`{recovery} min`"
                    )
                    if past_title:
                        st.caption(f"     {past_title}")


# ── PANEL: RAW RCA ──────────────────────────────────────────────────────────
def render_raw(artifacts: Dict[str, Any]) -> None:
    with st.expander("🔍 Raw RCA output (latest_rca.json)", expanded=False):
        rca = _get(artifacts, "rca")
        if not rca:
            st.caption("RCA artifact not available.")
            return
        st.caption(
            f"system_healthy=`{rca.get('system_healthy')}`  ·  "
            f"network_status=`{rca.get('network_status')}`  ·  "
            f"score=`{rca.get('network_score')}`"
        )
        st.json(rca)


# ── PUBLIC ENTRYPOINT ───────────────────────────────────────────────────────
def render(artifacts: Optional[Dict[str, Any]] = None) -> None:
    if artifacts is None:
        artifacts = al.load_all()

    st.subheader("🧠 Root Cause Analysis")
    render_summary(artifacts)
    st.markdown("---")
    render_incidents(artifacts)
    st.markdown("---")
    render_raw(artifacts)
