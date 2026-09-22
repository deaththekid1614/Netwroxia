#!/usr/bin/env python3
"""
Netwroxia — Phase E11: Combined Dashboard (app_v2.py)

Main entry point for the combined NOC dashboard. Preserves the visual
language of the original app.py while adding 8 new tabs from Phase E
sections.

Tabs (13 total):
   1. 🏠 Overview     — E2 section + old Router Health + Event Feed + Insight
   2. 📡 Beacons      — E3 section
   3. 🌐 Network      — reused topology_graph
   4. 💚 Health       — E4 section
   5. 💰 Impact       — E5 section
   6. 🔮 Predictions  — reused alert_card
   7. 🧠 RCA          — E6 section
   8. 🤖 Copilot      — preserved from app.py
   9. 🔧 Remediation  — E7 section
  10. 🧪 MLOps        — E8 section
  11. 📄 Reports      — E9 section
  12. 📋 Audit        — E10 section
  13. 📊 Metrics      — reused metric_chart

Run: streamlit run dashboard/app_v2.py
"""

import base64
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st
import streamlit.components.v1 as components

# ── PATH SETUP ──────────────────────────────────────────────────────────────
DASHBOARD_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = DASHBOARD_DIR.parent

for p in (str(DASHBOARD_DIR), str(PROJECT_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

# ── PAGE CONFIG ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Netwroxia NOC",
    page_icon="🏦",
    layout="wide",
    initial_sidebar_state="collapsed",
    menu_items={
        "About": "Netwroxia — Air-Gapped Predictive NOC Copilot for Banking. "
                 "IBM Z Datathon 2026."
    },
)

# ── AUTOREFRESH ─────────────────────────────────────────────────────────────
try:
    from streamlit_autorefresh import st_autorefresh
    _HAS_AUTOREFRESH = True
except ImportError:
    _HAS_AUTOREFRESH = False

# ── THEME ───────────────────────────────────────────────────────────────────
from utils.theme import inject_theme
inject_theme()

# ── AUTOREFRESH (after page config) ─────────────────────────────────────────
if _HAS_AUTOREFRESH:
    st_autorefresh(interval=999999999, limit=None, key="nx_live_refresh")
else:
    st.markdown('<meta http-equiv="refresh" content="999999999">', unsafe_allow_html=True)


# ── LOGO ────────────────────────────────────────────────────────────────────
def _load_logo_b64() -> Optional[str]:
    candidates = [
        DASHBOARD_DIR / "assets" / "netwroxia_logo.png",
        DASHBOARD_DIR / "assets" / "logo.png",
        DASHBOARD_DIR / "assets" / "logo.jpeg",
    ]
    for p in candidates:
        try:
            if p.exists():
                return base64.b64encode(p.read_bytes()).decode("ascii")
        except Exception:
            continue
    return None


NX_LOGO_B64 = _load_logo_b64()

# ── REUSED COMPONENTS ───────────────────────────────────────────────────────
from utils.influx_client import get_latest_by_router
from utils.pipeline_runner import (
    run_pipeline,
    get_pipeline_status,
    get_prediction_json,
    get_copilot_json,
)
from utils.artifact_loader import load_all as nx_load_all
from utils.nx_state import (
    ROUTERS,
    safe_num,
    safe_int,
    safe_str,
    derive_router_state,
    synthesize_events,
    synthesize_copilot_insight,
)
from components.alert_card import render_all_alerts
from components.metric_chart import render_metrics_tab, ensure_shared_snapshot
from components.topology_graph import render_topology_tab

# ── NEW SECTIONS ────────────────────────────────────────────────────────────
from sections import overview as nx_overview
from sections import beacons as nx_beacons
from sections import health as nx_health
from sections import impact as nx_impact
from sections import rca as nx_rca
from sections import remediation as nx_remediation
from sections import mlops as nx_mlops
from sections import reports as nx_reports
from sections import audit as nx_audit


# ═══════════════════════════════════════════════════════════════════════════
# LOAD DATA
# ═══════════════════════════════════════════════════════════════════════════
snapshot = get_latest_by_router() or {}
try:
    _shared_snap = ensure_shared_snapshot(hours=1) or {}
except Exception:
    _shared_snap = st.session_state.get("shared_router_snapshot", {}) or {}

pred_data = get_prediction_json() or {}
predictions = {
    p.get("router"): p
    for p in pred_data.get("predictions", [])
    if isinstance(p, dict)
}
copilot_data = get_copilot_json()

# ── DERIVE ROUTER STATES ────────────────────────────────────────────────────
shared = st.session_state.get("shared_router_snapshot", {}) or {}
router_states: List[Dict[str, Any]] = []
for r in ROUTERS:
    state = derive_router_state(r, snapshot, predictions, shared=shared)
    router_states.append(state)
    # Write back so metric_chart and prediction cards read consistent values
    shared.setdefault(r, {}).update({
        "latency_ms": state["latency_ms"],
        "packet_loss": state["packet_loss_pct"],
        "ospf_neighbors": state["ospf_neighbors"],
        "bgp_established": state["bgp_established"],
        "fault_prob": state["fault_prob"],
        "status": state["status"],
    })
st.session_state["shared_router_snapshot"] = shared

routers_at_risk = sum(1 for r in router_states if r["at_risk"])

if any(r["status"] == "CRITICAL" for r in router_states):
    overall_status = "CRITICAL"
elif any(r["status"] == "WARNING" for r in router_states):
    overall_status = "WARNING"
elif router_states and any(r["status"] == "HEALTHY" for r in router_states):
    overall_status = "HEALTHY"
else:
    overall_status = "UNKNOWN"

pipeline_status = get_pipeline_status() or {}
prediction_exists = pipeline_status.get(
    "prediction_exists", bool(predictions)
)
copilot_exists = pipeline_status.get("copilot_exists", bool(copilot_data))


# ═══════════════════════════════════════════════════════════════════════════
# HEADER
# ═══════════════════════════════════════════════════════════════════════════
if NX_LOGO_B64:
    logo_html = (
        f'<img src="data:image/png;base64,{NX_LOGO_B64}" '
        f'style="width:52px;height:52px;border-radius:12px;object-fit:cover;'
        f'box-shadow:0 0 24px rgba(34,211,238,0.35);" alt="Netwroxia"/>'
    )
else:
    logo_html = '<div class="nx-logo">🏦</div>'

st.markdown(f"""
<div class="nx-header">
  <div class="nx-brand">
    {logo_html}
    <div>
      <div class="nx-title">Netwroxia <span style="color:#22d3ee;">NOC</span></div>
      <div class="nx-sub">Predict · Prevent · Protect — Banking Network Copilot</div>
    </div>
  </div>
  <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;justify-content:flex-end;">
    <span class="nx-pill ok">
      <span class="nx-dot"></span> AIR-GAPPED · OFFLINE
    </span>
    <span class="nx-pill brand">
      ◉ LIVE · <span id="nx-clock">--:--:--</span>
    </span>
    <span class="nx-pill">NETWROXIA</span>
    <span class="nx-pill">IBM Z · 2026</span>
  </div>
</div>
""", unsafe_allow_html=True)

# Client-side clock — 0px iframe so it never occupies layout space
components.html("""
<script>
  function nxTick(){
    const d = new Date();
    const p = n => String(n).padStart(2,'0');
    const el = window.parent.document.getElementById('nx-clock');
    if (el) el.textContent = p(d.getHours())+':'+p(d.getMinutes())+':'+p(d.getSeconds());
  }
  nxTick();
  setInterval(nxTick, 1000);
</script>
""", height=0, width=0)

st.write("")


# ═══════════════════════════════════════════════════════════════════════════
# STATUS BAR
# ═══════════════════════════════════════════════════════════════════════════
last_update = st.session_state.get("nx_last_pipeline_ts", "--")

c1, c2, c3, c4, c5 = st.columns(5)
with c1: st.metric("System Status", overall_status)
with c2: st.metric("Routers at Risk", routers_at_risk)
with c3: st.metric("Last Update", last_update)
with c4: st.metric("Prediction", "Available" if prediction_exists else "None")
with c5: st.metric("Copilot", "Available" if copilot_exists else "None")

st.write("")


# ═══════════════════════════════════════════════════════════════════════════
# RUN PIPELINE BUTTON
# ═══════════════════════════════════════════════════════════════════════════
run_col, cap_col = st.columns([3, 2])
with run_col:
    run_clicked = st.button("🚀 RUN PIPELINE", type="primary")
with cap_col:
    st.markdown(
        "<div style='padding-top:14px;font-family:JetBrains Mono,monospace;"
        "font-size:11px;color:#64748b;letter-spacing:0.1em;'>"
        "~60s · FULLY OFFLINE INFERENCE · ZERO CLOUD CALLS</div>",
        unsafe_allow_html=True,
    )

if run_clicked:
    with st.spinner("Running pipeline... (~60 seconds)"):
        ok, results = run_pipeline(verbose=False)
    if ok:
        st.session_state["nx_last_pipeline_ts"] = datetime.now().strftime("%H:%M:%S")
        try:
            snapshot = get_latest_by_router() or {}
            pred_data = get_prediction_json() or {}
            predictions = {
                p.get("router"): p
                for p in pred_data.get("predictions", [])
                if isinstance(p, dict)
            }
            fresh_states = [
                derive_router_state(r, snapshot, predictions, shared=shared)
                for r in ROUTERS
            ]
        except Exception:
            fresh_states = router_states
        st.session_state["nx_events"] = synthesize_events(fresh_states)
        st.session_state["nx_synth_insight"] = synthesize_copilot_insight(fresh_states)
        st.success("✅ Pipeline complete! Refreshing...")
        st.balloons()
        try:
            st.rerun()
        except Exception:
            try:
                st.experimental_rerun()
            except Exception:
                st.info("Please refresh the page manually.")
    else:
        st.error("❌ Pipeline failed.")
        for r in results:
            if not r["success"]:
                st.code(f"{r['name']}: {r.get('error', 'Unknown')[:200]}")

st.divider()


# ═══════════════════════════════════════════════════════════════════════════
# COPILOT PARSING (real data preferred; synthesized fallback)
# ═══════════════════════════════════════════════════════════════════════════
responses: List[Dict[str, Any]] = []
if copilot_data:
    if isinstance(copilot_data, list):
        responses = copilot_data
    elif isinstance(copilot_data, dict):
        for key in ("responses", "results", "data"):
            if key in copilot_data and isinstance(copilot_data[key], list):
                responses = copilot_data[key]
                break
        if not responses and "predicted_issue" in copilot_data:
            responses = [copilot_data]

synth_insight = st.session_state.get("nx_synth_insight")
if not responses and not synth_insight:
    try:
        synth_insight = synthesize_copilot_insight(router_states)
        st.session_state["nx_synth_insight"] = synth_insight
    except Exception:
        synth_insight = None
if not responses and synth_insight:
    responses = [synth_insight]


# ═══════════════════════════════════════════════════════════════════════════
# LOAD ARTIFACTS FOR SECTIONS (via E1)
# ═══════════════════════════════════════════════════════════════════════════
nx_artifacts = nx_load_all()


# ═══════════════════════════════════════════════════════════════════════════
# TABS
# ═══════════════════════════════════════════════════════════════════════════
(
    tab_overview, tab_beacons, tab_network, tab_health, tab_impact,
    tab_predictions, tab_rca, tab_copilot, tab_remediation, tab_mlops,
    tab_reports, tab_audit, tab_metrics,
) = st.tabs([
    "🏠 Overview",
    "📡 Beacons",
    "🌐 Network",
    "💚 Health",
    "💰 Impact",
    "🔮 Predictions",
    "🧠 RCA",
    "🤖 Copilot",
    "🔧 Remediation",
    "🧪 MLOps",
    "📄 Reports",
    "📋 Audit",
    "📊 Metrics",
])


# ── Tab 1: Overview ─────────────────────────────────────────────────────────
with tab_overview:
    # New section: executive + health + beacons + incidents summary
    nx_overview.render(nx_artifacts)
    st.markdown("---")

    # Old Router Health cards (preserved)
    st.markdown(
        '<div class="nx-section"><div class="nx-section-bar"></div>'
        '<div class="nx-section-title">Router Health</div>'
        '<div class="nx-section-kicker">// Real-time fleet posture</div></div>',
        unsafe_allow_html=True,
    )
    for s in router_states:
        fault_pct = max(0.0, min(100.0, s["fault_prob"] * 100))
        _bgp_raw = s["bgp_established"]
        if _bgp_raw is None:
            bgp_txt = "--"
        elif isinstance(_bgp_raw, bool):
            bgp_txt = "UP" if _bgp_raw else "DOWN"
        else:
            bgp_txt = ("DOWN" if str(_bgp_raw).strip().lower()
                       in {"false", "0", "down", "no"} else "UP")

        lat_txt = safe_num(s["latency_ms"], "{:.2f}")
        pkt_txt = safe_num(s["packet_loss_pct"], "{:.1f}")
        ospf_txt = safe_int(s["ospf_neighbors"], "--")
        bgp_color = ("#4ade80" if bgp_txt == "UP"
                     else ("#94a3b8" if bgp_txt == "--" else "#f87171"))

        st.markdown(f"""
        <div class="nx-router" style="--stripe: {s['stripe']};">
          <div>
            <div class="nx-router-name">{s['router']}</div>
            <span class="nx-status-chip"><span class="nx-dot"></span>{s['status']}</span>
          </div>
          <div class="nx-metric">
            <div class="nx-metric-label">Latency</div>
            <div class="nx-metric-value">{lat_txt} <span style="font-size:11px;color:#64748b;">ms</span></div>
          </div>
          <div class="nx-metric">
            <div class="nx-metric-label">Packet Loss</div>
            <div class="nx-metric-value">{pkt_txt}<span style="font-size:11px;color:#64748b;">%</span></div>
          </div>
          <div class="nx-metric">
            <div class="nx-metric-label">OSPF</div>
            <div class="nx-metric-value">{ospf_txt}</div>
          </div>
          <div class="nx-metric">
            <div class="nx-metric-label">BGP</div>
            <div class="nx-metric-value" style="color: {bgp_color};">{bgp_txt}</div>
          </div>
          <div class="nx-metric" style="flex:1.4;">
            <div class="nx-metric-label">Fault Probability</div>
            <div class="nx-metric-value">{fault_pct:.1f}<span style="font-size:11px;color:#64748b;">%</span></div>
            <div class="nx-bar"><div style="width:{fault_pct:.1f}%;"></div></div>
          </div>
        </div>
        """, unsafe_allow_html=True)

    # Old Live Event Feed (preserved)
    st.markdown(
        '<div class="nx-section"><div class="nx-section-bar"></div>'
        '<div class="nx-section-title">Live Event Feed</div>'
        '<div class="nx-section-kicker">// Streaming telemetry</div></div>',
        unsafe_allow_html=True,
    )
    events = st.session_state.get("nx_events", [])
    if not events:
        try:
            events = synthesize_events(router_states)
            st.session_state["nx_events"] = events
        except Exception:
            events = []
    if events:
        rows = "".join(
            f'<div class="nx-event"><div class="nx-event-time">{e["time"]}</div>'
            f'<div class="nx-event-dot {e["kind"]}"></div>'
            f'<div class="nx-event-msg">{e["msg"]}</div></div>'
            for e in events
        )
        st.markdown(f'<div class="nx-feed">{rows}</div>',
                    unsafe_allow_html=True)
    else:
        st.info("Run pipeline to generate telemetry events.")

    # Old Copilot Insight card (preserved)
    st.markdown(
        '<div class="nx-section"><div class="nx-section-bar"></div>'
        '<div class="nx-section-title">Latest Copilot Insight</div>'
        '<div class="nx-section-kicker">// AI reasoning</div></div>',
        unsafe_allow_html=True,
    )
    if responses:
        r = responses[0]
        issue = safe_str(r.get("predicted_issue"), "No active issues")
        root = safe_str(r.get("root_cause"), "N/A")[:280]
        qfix = safe_str(r.get("quick_fix"), "N/A")
        dfix = safe_str(r.get("deep_fix"), "N/A")
        st.markdown(f"""
        <div class="nx-copilot">
          <div class="nx-copilot-header">
            <div class="nx-copilot-icon">🤖</div>
            <div>
              <div class="nx-copilot-title">{issue}</div>
              <div style="font-family:'JetBrains Mono',monospace;font-size:10px;color:#a855f7;
                          text-transform:uppercase;letter-spacing:0.12em;">Netwroxia Copilot · Air-Gapped LLM</div>
            </div>
          </div>
          <div class="nx-copilot-row"><b>🎯 Root Cause</b>{root}</div>
          <div class="nx-copilot-row"><b>🔧 Quick Fix</b>{qfix}</div>
          <div class="nx-copilot-row"><b>🛠️ Deep Fix</b>{dfix}</div>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.info("Run pipeline to generate copilot insight.")


# ── Tab 2: Beacons ──────────────────────────────────────────────────────────
with tab_beacons:
    nx_beacons.render(nx_artifacts)


# ── Tab 3: Network ──────────────────────────────────────────────────────────
with tab_network:
    render_topology_tab()


# ── Tab 4: Health ───────────────────────────────────────────────────────────
with tab_health:
    nx_health.render(nx_artifacts)


# ── Tab 5: Impact ───────────────────────────────────────────────────────────
with tab_impact:
    nx_impact.render(nx_artifacts)


# ── Tab 6: Predictions ──────────────────────────────────────────────────────
with tab_predictions:
    render_all_alerts()


# ── Tab 7: RCA ──────────────────────────────────────────────────────────────
with tab_rca:
    nx_rca.render(nx_artifacts)


# ── Tab 8: Copilot (full) ───────────────────────────────────────────────────
with tab_copilot:
    st.markdown(
        '<div class="nx-section"><div class="nx-section-bar"></div>'
        '<div class="nx-section-title">Full Copilot Analysis</div>'
        '<div class="nx-section-kicker">// End-to-end incident reasoning</div></div>',
        unsafe_allow_html=True,
    )
    if responses:
        for i, resp in enumerate(responses):
            title = safe_str(resp.get("predicted_issue"), f"Analysis {i+1}")
            urgency = safe_str(resp.get("urgency"), "—")
            u = urgency.upper()
            if "CRIT" in u or "HIGH" in u:
                u_class, u_emoji = "urgent", "🚨"
            elif "MED" in u or "WARN" in u:
                u_class, u_emoji = "warn", "⚠️"
            else:
                u_class, u_emoji = "ok", "ℹ️"

            with st.expander(f"{u_emoji}  {title}  —  {urgency}",
                             expanded=(i == 0)):
                c1, c2, c3, c4 = st.columns(4)
                with c1:
                    st.markdown(
                        f'<div class="nx-tile"><div class="nx-tile-label">Confidence</div>'
                        f'<div class="nx-tile-value">{safe_str(resp.get("confidence"), "—")}</div></div>',
                        unsafe_allow_html=True)
                with c2:
                    st.markdown(
                        f'<div class="nx-tile"><div class="nx-tile-label">Urgency</div>'
                        f'<div class="nx-tile-value {u_class}">{urgency}</div></div>',
                        unsafe_allow_html=True)
                with c3:
                    st.markdown(
                        f'<div class="nx-tile"><div class="nx-tile-label">Affected Users</div>'
                        f'<div class="nx-tile-value">{safe_str(resp.get("affected_users"), "—")}</div></div>',
                        unsafe_allow_html=True)
                with c4:
                    st.markdown(
                        f'<div class="nx-tile"><div class="nx-tile-label">Time to Impact</div>'
                        f'<div class="nx-tile-value warn">{safe_str(resp.get("time_to_impact_min"), "—")} min</div></div>',
                        unsafe_allow_html=True)

                st.write("")
                sites = resp.get("affected_sites", []) or []
                svcs = resp.get("affected_services", []) or []
                s1, s2 = st.columns(2)
                with s1:
                    st.markdown(f"**🏢 Affected Sites:** "
                                f"{', '.join(sites) if sites else '_—_'}")
                with s2:
                    st.markdown(f"**⚙️ Affected Services:** "
                                f"{', '.join(svcs) if svcs else '_—_'}")

                st.markdown("**🎯 Root Cause**")
                st.write(safe_str(resp.get("root_cause"), "—"))

                st.markdown("**✅ Recommended Actions**")
                for action in (resp.get("recommended_actions") or []):
                    st.markdown(f"- {action}")

                st.markdown(f"**🔧 Quick Fix:** `{safe_str(resp.get('quick_fix'), '—')}`")
                st.markdown(f"**🛠️ Deep Fix:** `{safe_str(resp.get('deep_fix'), '—')}`")
                if resp.get("rbi_compliance_note"):
                    st.markdown(f"**🏛️ RBI Compliance:** "
                                f"{resp.get('rbi_compliance_note')}")
    else:
        st.info("Run pipeline to generate copilot analysis.")


# ── Tab 9: Remediation ──────────────────────────────────────────────────────
with tab_remediation:
    nx_remediation.render(nx_artifacts)


# ── Tab 10: MLOps ───────────────────────────────────────────────────────────
with tab_mlops:
    nx_mlops.render(nx_artifacts)


# ── Tab 11: Reports ─────────────────────────────────────────────────────────
with tab_reports:
    nx_reports.render(nx_artifacts)


# ── Tab 12: Audit ───────────────────────────────────────────────────────────
with tab_audit:
    nx_audit.render(nx_artifacts)


# ── Tab 13: Metrics ─────────────────────────────────────────────────────────
with tab_metrics:
    render_metrics_tab()


# ═══════════════════════════════════════════════════════════════════════════
# FOOTER
# ═══════════════════════════════════════════════════════════════════════════
st.divider()
st.markdown("""
<div class="nx-footer">
  <span class="nx-pill">NETWROXIA v2.0</span>
  <span class="nx-pill brand">IBM Z DATATHON 2026</span>
  <span class="nx-pill">NETWROXIA · NOC</span>
  <span class="nx-pill ok">🔒 100% AIR-GAPPED</span>
</div>
""", unsafe_allow_html=True)
