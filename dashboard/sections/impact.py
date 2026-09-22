#!/usr/bin/env python3
"""
Netwroxia — Phase E5: Impact Section

Banking service impact + financial damage + RBI compliance.
Merges B3 (service impact) and B4 (business impact). Reads through E1.

Usage:
    from dashboard.sections import impact
    impact.render()
    impact.render(artifacts={...})
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


def _fmt_inr(value: Any) -> str:
    if value is None:
        return "₹0"
    try:
        n = float(value)
    except (TypeError, ValueError):
        return str(value)
    if n == 0:
        return "₹0"
    a = abs(n)
    if a < 1000:
        return f"₹{int(n)}"
    if a < 100_000:
        return f"₹{n/1000:.1f}K"
    if a < 10_000_000:
        return f"₹{n/100_000:.2f}L"
    return f"₹{n/10_000_000:.2f}Cr"


def _display_str(value: Any, fallback: str = "n/a") -> str:
    if value is None or value == "":
        return fallback
    return str(value)


# ── PANEL: EXECUTIVE BANNER ─────────────────────────────────────────────────
def render_banner(artifacts: Dict[str, Any]) -> None:
    impact = _get(artifacts, "impact")
    biz = _get(artifacts, "business_impact")

    if not impact and not biz:
        st.info("Impact artifacts not available. "
                "Run: `python3 impact/impact_analyzer.py` and "
                "`python3 impact/business_impact.py`")
        return

    # Top-level
    sev = impact.get("overall_impact", {}).get("severity") \
        if impact else biz.get("source_severity")
    sev = sev or "UNKNOWN"
    emoji = _severity_emoji(sev)

    st.markdown(f"### {emoji} Overall Severity: **{sev}**")

    tier = biz.get("current_loss", {}).get("tier") if biz else None
    loss_min = biz.get("current_loss", {}).get("per_minute_display") if biz else None
    loss_hour = biz.get("current_loss", {}).get("per_hour_display") if biz else None
    rbi_pen = biz.get("rbi_compliance", {}).get("estimated_penalty_display") if biz else None
    ew = biz.get("value_of_early_warning", {}).get("prevented_loss_display") if biz else None

    cols = st.columns(5)
    cols[0].metric("Tier", _display_str(tier))
    cols[1].metric("Loss rate", _display_str(loss_min))
    cols[2].metric("Loss/hour", _display_str(loss_hour))
    cols[3].metric("RBI penalty est.", _display_str(rbi_pen))
    cols[4].metric("Early-warning value", _display_str(ew))


# ── PANEL: AFFECTED ROUTERS ─────────────────────────────────────────────────
def render_affected_routers(artifacts: Dict[str, Any]) -> None:
    st.markdown("#### Affected Routers")
    impact = _get(artifacts, "impact")
    routers = impact.get("affected_routers", {}) if impact else {}

    if not routers:
        st.caption("No routers affected.")
        return

    rows = []
    for name, entry in routers.items():
        rows.append({
            "Router": name,
            "Status": entry.get("status", "?"),
            "Score": entry.get("score", 0),
            "Direct": "yes" if entry.get("directly_affected") else "",
            "Via": entry.get("via_propagation_from") or "",
            "Chain": " → ".join(entry.get("propagation_chain") or []),
            "Users": entry.get("user_count", 0),
            "₹/min": _fmt_inr(entry.get("revenue_per_min_inr")),
        })

    try:
        import pandas as pd  # noqa
        st.dataframe(rows, use_container_width=True, hide_index=True)
    except Exception:
        for r in rows:
            st.markdown(f"- `{r['Router']}` — {r['Status']} "
                        f"({'direct' if r['Direct'] else 'propagated'})")


# ── PANEL: AFFECTED SERVICES ────────────────────────────────────────────────
def render_affected_services(artifacts: Dict[str, Any]) -> None:
    st.markdown("#### Affected Services")
    impact = _get(artifacts, "impact")
    services = impact.get("affected_services", []) if impact else []

    if not services:
        st.caption("No services affected.")
        return

    rows = []
    for svc in services:
        rows.append({
            "Service": svc.get("service_id", "?"),
            "Name": svc.get("name", "?"),
            "Availability": svc.get("availability", "?"),
            "Criticality": svc.get("criticality", "?"),
            "RBI": "yes" if svc.get("rbi_mandated") else "",
            "SLA target": f"{svc.get('sla_uptime_pct', 0)}%",
            "Hosts hit": f"{len(svc.get('affected_hosts', []))}/"
                          f"{svc.get('host_count_total', 0)}",
            "₹/min": _fmt_inr(svc.get("revenue_per_min_inr")),
        })

    try:
        import pandas as pd  # noqa
        st.dataframe(rows, use_container_width=True, hide_index=True)
    except Exception:
        for r in rows:
            st.markdown(f"- `{r['Service']}` ({r['Availability']})")


# ── PANEL: PROJECTIONS ──────────────────────────────────────────────────────
def render_projections(artifacts: Dict[str, Any]) -> None:
    st.markdown("#### Projections (if unrecovered)")
    biz = _get(artifacts, "business_impact")
    projections = biz.get("projections", {}) if biz else {}

    if not projections:
        st.caption("No projections.")
        return

    # Sort by horizon key min_N
    keys = sorted(
        projections.keys(),
        key=lambda k: projections[k].get("minutes", 0)
        if isinstance(projections[k], dict) else 0,
    )

    cols = st.columns(len(keys) or 1)
    for i, key in enumerate(keys):
        entry = projections[key]
        if isinstance(entry, dict):
            minutes = entry.get("minutes", "?")
            display = entry.get("display", "n/a")
            cols[i].metric(f"+{minutes} min", display)
        else:
            cols[i].metric(key, str(entry))


# ── PANEL: RBI COMPLIANCE ───────────────────────────────────────────────────
def render_rbi(artifacts: Dict[str, Any]) -> None:
    st.markdown("#### RBI Compliance Risk")
    biz = _get(artifacts, "business_impact")
    rbi = biz.get("rbi_compliance", {}) if biz else {}

    if not rbi or not rbi.get("applies"):
        st.success("No RBI-mandated services affected. No compliance risk.")
        return

    cols = st.columns(3)
    cols[0].metric("Applies", "yes")
    cols[1].metric("Services at risk", len(rbi.get("projected_breaches", [])))
    cols[2].metric("Estimated penalty",
                   _display_str(rbi.get("estimated_penalty_display")))

    breaches = rbi.get("projected_breaches", [])
    if breaches:
        rows = []
        for b in breaches:
            rows.append({
                "Service": b.get("service_id", "?"),
                "Criticality": b.get("criticality", "?"),
                "Availability": b.get("availability", "?"),
                "Breach in (min)": b.get("minutes_to_breach", "?"),
                "Base penalty": _fmt_inr(b.get("base_penalty_inr")),
            })
        try:
            import pandas as pd  # noqa
            st.dataframe(rows, use_container_width=True, hide_index=True)
        except Exception:
            for r in rows:
                st.markdown(f"- `{r['Service']}` breach in "
                            f"{r['Breach in (min)']} min")

    narrative = rbi.get("narrative")
    if narrative:
        st.info(narrative)


# ── PANEL: PROPAGATION CHAINS ───────────────────────────────────────────────
def render_propagation(artifacts: Dict[str, Any]) -> None:
    impact = _get(artifacts, "impact")
    chains = impact.get("propagation_chains", []) if impact else []

    if not chains:
        return

    st.markdown("#### Propagation Chains")
    for c in chains:
        root = c.get("root", "?")
        downstream = c.get("downstream", [])
        count = c.get("downstream_count", 0)
        users = c.get("total_users_downstream", 0)
        if downstream:
            st.markdown(
                f"**{root}** → {', '.join(downstream)}  "
                f"({count} routers, {users} users)"
            )
        else:
            st.markdown(f"**{root}** — isolated (no downstream)")


# ── PUBLIC ENTRYPOINT ───────────────────────────────────────────────────────
def render(artifacts: Optional[Dict[str, Any]] = None) -> None:
    if artifacts is None:
        artifacts = al.load_all()

    st.subheader("💰 Banking Service Impact")
    render_banner(artifacts)
    st.markdown("---")
    render_affected_routers(artifacts)
    st.markdown("---")
    render_affected_services(artifacts)
    st.markdown("---")
    render_projections(artifacts)
    st.markdown("---")
    render_rbi(artifacts)
    st.markdown("---")
    render_propagation(artifacts)
