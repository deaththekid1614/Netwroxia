#!/usr/bin/env python3
"""
Netwroxia — Phase E7: Remediation Section

Pending approvals, incident states, action history. Reads JSON
artifacts through E1, and reads incidents.db directly via C2's
incident_store (SQLite, not JSON).

Usage:
    from dashboard.sections import remediation
    remediation.render()
    remediation.render(artifacts={...})
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

# Lazy imports — the section should still render if C2 isn't importable
try:
    from incidents import incident_store as store
    from audit import audit_logger as audit
    _C2_AVAILABLE = True
except Exception:
    store = None
    audit = None
    _C2_AVAILABLE = False


# ── HELPERS ─────────────────────────────────────────────────────────────────
def _get(artifacts: Dict[str, Any], name: str) -> Dict[str, Any]:
    data = artifacts.get(name)
    if not isinstance(data, dict):
        return {}
    if data.get("_missing") or data.get("_error"):
        return {}
    return data


def _state_emoji(state: str) -> str:
    return {
        "DETECTED": "🔵",
        "INVESTIGATING": "🟡",
        "AWAITING_APPROVAL": "🟠",
        "EXECUTING": "🟣",
        "VERIFYING": "🟣",
        "VERIFIED": "🟢",
        "RESOLVED": "🟢",
        "FAILED": "🔴",
        "ROLLED_BACK": "🟠",
        "ESCALATED": "🔴",
        "CLOSED": "⚫",
    }.get((state or "").upper(), "⚪")


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


# ── PANEL: PENDING APPROVALS ────────────────────────────────────────────────
def render_approvals(artifacts: Dict[str, Any]) -> None:
    st.markdown("#### ⏳ Pending Approvals (human action required)")

    pending = _get(artifacts, "pending_approvals")
    entries = pending.get("pending", {}) if pending else {}

    if not entries:
        st.success("No pending approvals. Auto-remediation is unblocked.")
        return

    rows = []
    for iid, entry in entries.items():
        rows.append({
            "Incident": iid,
            "Severity": entry.get("severity", "?"),
            "Router": entry.get("root_router", "?"),
            "Action": entry.get("action", "?"),
            "Decision": entry.get("decision", "?"),
            "Requested": _age_str(entry.get("requested_at")),
            "By": entry.get("requested_by", "?"),
        })

    try:
        import pandas as pd  # noqa
        st.dataframe(rows, use_container_width=True, hide_index=True)
    except Exception:
        for r in rows:
            st.markdown(f"- `{r['Incident']}` — {r['Action']} "
                        f"({r['Severity']})")


# ── PANEL: INCIDENT STATES ──────────────────────────────────────────────────
def _load_incidents() -> List[Dict[str, Any]]:
    if not _C2_AVAILABLE:
        return []
    try:
        return store.list_incidents(limit=100)
    except Exception:
        return []


def render_incident_states() -> None:
    st.markdown("#### Incident Lifecycle")

    if not _C2_AVAILABLE:
        st.info("C2 incident_store unavailable. "
                "Check `incidents/incident_store.py`.")
        return

    incidents = _load_incidents()
    if not incidents:
        st.caption("No incidents in DB.")
        return

    # Counts by state
    counts: Dict[str, int] = {}
    for inc in incidents:
        s = inc.get("state", "UNKNOWN")
        counts[s] = counts.get(s, 0) + 1

    state_order = [
        "DETECTED", "INVESTIGATING", "AWAITING_APPROVAL",
        "EXECUTING", "VERIFYING", "VERIFIED",
        "RESOLVED", "FAILED", "ROLLED_BACK", "ESCALATED", "CLOSED",
    ]
    cols = st.columns(min(len([s for s in state_order if s in counts]) or 1, 6))
    shown = 0
    for state in state_order:
        if state not in counts:
            continue
        if shown >= 6:
            break
        emoji = _state_emoji(state)
        cols[shown].metric(f"{emoji} {state}", counts[state])
        shown += 1

    # Table of recent incidents
    st.markdown("**Recent incidents (max 20)**")
    rows = []
    for inc in incidents[:20]:
        rows.append({
            "ID": inc.get("incident_id", "?")[:16],
            "State": inc.get("state", "?"),
            "Severity": inc.get("severity", "?"),
            "Root": inc.get("root_router", "?"),
            "Updated": _age_str(inc.get("updated_at")),
        })
    try:
        import pandas as pd  # noqa
        st.dataframe(rows, use_container_width=True, hide_index=True)
    except Exception:
        for r in rows:
            st.markdown(f"- `{r['ID']}` — {r['State']} ({r['Severity']})")


# ── PANEL: RECENT ACTIONS ───────────────────────────────────────────────────
def _recent_actions(limit: int = 30) -> List[Dict[str, Any]]:
    if not _C2_AVAILABLE:
        return []
    try:
        events = audit.tail(n=limit * 3)
    except Exception:
        return []
    action_events = [
        e for e in events
        if (e.get("event") or "").startswith("action.")
    ]
    return action_events[-limit:]


def render_actions() -> None:
    st.markdown("#### Recent Actions (audit trail)")

    if not _C2_AVAILABLE:
        st.info("C1 audit_logger unavailable.")
        return

    events = _recent_actions(30)
    if not events:
        st.caption("No action.* events in audit log yet.")
        return

    rows = []
    for e in events:
        rows.append({
            "When": _age_str(e.get("ts")),
            "Event": e.get("event", "?"),
            "Incident": (e.get("incident_id") or "-")[:16],
            "Router": e.get("router") or "-",
            "Actor": e.get("actor") or "-",
        })
    try:
        import pandas as pd  # noqa
        st.dataframe(rows, use_container_width=True, hide_index=True)
    except Exception:
        for r in rows:
            st.markdown(f"- `{r['When']}` {r['Event']} on {r['Router']}")


# ── PUBLIC ENTRYPOINT ───────────────────────────────────────────────────────
def render(artifacts: Optional[Dict[str, Any]] = None) -> None:
    if artifacts is None:
        artifacts = al.load_all()

    st.subheader("🔧 Remediation")
    render_approvals(artifacts)
    st.markdown("---")
    render_incident_states()
    st.markdown("---")
    render_actions()
