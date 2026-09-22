#!/usr/bin/env python3
"""
Netwroxia — Phase E4: Health Section

Detailed network health score view. Reads through E1's artifact
loader. Renders per-router scores, status, and the component
breakdown that produced them (beacon, ML, safety caps, floors).

Usage:
    from dashboard.sections import health
    health.render()
    health.render(artifacts={...})
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
def _fmt(value: Any, decimals: int = 1, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.{decimals}f}{suffix}"
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
        "HEALTHY": "🟢",
        "WARNING": "🟡",
        "CRITICAL": "🔴",
        "DOWN": "⚫",
    }.get(status, "⚪")


def _flag_badges(components: Dict[str, Any]) -> str:
    """Render inline badges for notable component flags."""
    badges: List[str] = []
    if components.get("beacon_inferred"):
        badges.append("`INFERRED`")
    if components.get("ml_mismatch"):
        badges.append("`ML_MISMATCH`")
    if components.get("credible_floor_applied"):
        badges.append(f"`FLOOR={components.get('credible_floor')}`")
    cap = components.get("safety_cap_applied")
    if cap:
        badges.append(f"`CAP={cap}`")
    return "  ".join(badges)


# ── PANEL: NETWORK SUMMARY ──────────────────────────────────────────────────
def render_summary(artifacts: Dict[str, Any]) -> None:
    health = _get(artifacts, "health")
    if not health:
        st.info("Health artifact not available. "
                "Run: `python3 analytics/health_score.py`")
        return

    net = health.get("network", {})
    status = net.get("status", "UNKNOWN")
    emoji = _status_emoji(status)

    cols = st.columns(4)
    cols[0].metric("Score", _fmt(net.get("score"), 1))
    cols[1].metric("Status", f"{emoji} {status}")
    cols[2].metric("Routers", net.get("router_count", "n/a"))
    cols[3].metric("Regions", net.get("region_count", "n/a"))

    ts = health.get("timestamp")
    if ts:
        st.caption(f"Updated: {ts}")


# ── PANEL: ROUTER MATRIX ────────────────────────────────────────────────────
def render_matrix(artifacts: Dict[str, Any]) -> None:
    st.markdown("#### Router Status Matrix")
    health = _get(artifacts, "health")
    routers = health.get("routers", {}) if health else {}
    if not routers:
        st.caption("No routers in health artifact.")
        return

    rows = []
    for name, entry in routers.items():
        components = entry.get("components", {}) or {}
        rows.append({
            "Router": name,
            "Role": entry.get("role", "?"),
            "Status": entry.get("status", "?"),
            "Score": entry.get("score", 0),
            "Beacon": components.get("beacon_status", "?"),
            "ML mismatch": "yes" if components.get("ml_mismatch") else "",
            "Floor applied": "yes" if components.get("credible_floor_applied") else "",
            "Cap": components.get("safety_cap_applied") or "",
        })

    try:
        import pandas as pd  # noqa
        st.dataframe(rows, use_container_width=True, hide_index=True)
    except Exception:
        for r in rows:
            st.markdown(f"- `{r['Router']}` — {r['Status']} ({r['Score']})")


# ── PANEL: PER-ROUTER DETAILS ───────────────────────────────────────────────
def render_router_details(artifacts: Dict[str, Any]) -> None:
    st.markdown("#### Per-Router Detail")
    health = _get(artifacts, "health")
    routers = health.get("routers", {}) if health else {}
    if not routers:
        return

    # Sort: worst score first
    ordered = sorted(
        routers.items(),
        key=lambda kv: kv[1].get("score", 999),
    )

    for name, entry in ordered:
        status = entry.get("status", "UNKNOWN")
        emoji = _status_emoji(status)
        score = entry.get("score", 0)
        components = entry.get("components", {}) or {}
        badges = _flag_badges(components)

        header = (
            f"{emoji} {name} — {status} "
            f"(score {score})"
            + (f"  ·  {badges}" if badges else "")
        )

        with st.expander(header, expanded=False):
            c1, c2, c3 = st.columns(3)
            c1.metric("Beacon status", components.get("beacon_status", "?"))
            c2.metric(
                "Beacon penalty",
                f"-{components.get('beacon_penalty', 0)}"
            )
            c3.metric(
                "Beacon loss %",
                _fmt(components.get("beacon_loss_pct"), 2)
            )

            c4, c5, c6 = st.columns(3)
            c4.metric(
                "XGBoost prob",
                _fmt(components.get("xgboost_fault_prob"), 4)
            )
            c5.metric(
                "LSTM future prob",
                _fmt(components.get("lstm_future_prob"), 4)
            )
            c6.metric(
                "ML total penalty",
                _fmt(components.get("ml_total_penalty"), 2)
            )

            if components.get("credible_floor"):
                st.caption(
                    f"Credible floor: `{components.get('credible_floor')}`  ·  "
                    f"applied: `{components.get('credible_floor_applied')}`  ·  "
                    f"BGP established: `{components.get('bgp_established')}`"
                )

            if components.get("safety_cap_applied"):
                st.warning(
                    f"Safety cap applied: `{components['safety_cap_applied']}`"
                )

            if components.get("ml_mismatch"):
                st.info(
                    "ML_MISMATCH: beacon says OK, but ML shows elevated "
                    "fault probability. Treat as a model/data-quality "
                    "flag, not a network failure."
                )

            if components.get("beacon_inferred"):
                st.caption(
                    "Beacon status was INFERRED — this router is the "
                    "beacon receiver (HO), not a sender. Status derived "
                    "from senders."
                )


# ── PANEL: REGIONS ──────────────────────────────────────────────────────────
def render_regions(artifacts: Dict[str, Any]) -> None:
    health = _get(artifacts, "health")
    regions = health.get("regions", {}) if health else {}
    if not regions:
        return

    st.markdown("#### Regions")
    for name, data in regions.items():
        emoji = _status_emoji(data.get("status", "UNKNOWN"))
        st.markdown(
            f"{emoji} **{name}** — score `{_fmt(data.get('score'), 1)}` "
            f"({data.get('status', '?')})  ·  "
            f"routers={data.get('router_count', '?')}  ·  "
            f"users={data.get('user_count', '?')}"
        )


# ── PUBLIC ENTRYPOINT ───────────────────────────────────────────────────────
def render(artifacts: Optional[Dict[str, Any]] = None) -> None:
    if artifacts is None:
        artifacts = al.load_all()

    st.subheader("💚 Network Health Score")
    render_summary(artifacts)
    st.markdown("---")
    render_matrix(artifacts)
    st.markdown("---")
    render_router_details(artifacts)
    st.markdown("---")
    render_regions(artifacts)
