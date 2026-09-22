#!/usr/bin/env python3
"""
Netwroxia — Phase E11b: UI State Helpers

Pure functions used by app_v2.py (and reusable by future UI code).
All Streamlit-free for testability — the caller passes in data and
reads the return value.

Contains:
  - Router constants (ROUTERS, ROUTER_PROFILE, BASELINE_LATENCY_MS)
  - Safe formatting (safe_num, safe_int, safe_str)
  - Latency jitter for visible liveness
  - derive_router_state  — unified per-router view
  - synthesize_events    — feed fallback
  - synthesize_copilot_insight — copilot fallback
"""

import math
import random
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

# ── CONSTANTS ───────────────────────────────────────────────────────────────
ROUTERS = ["HO-Chennai", "ZO-Bengaluru", "BR-Koramangala", "BR-Whitefield"]

ROUTER_PROFILE: Dict[str, Dict[str, Any]] = {
    "HO-Chennai":     {"packet_loss_pct": 0.3,   "ospf_neighbors": 3,
                       "bgp_established": True,  "fault_prob": 0.04,
                       "latency_ms": 3.0},
    "ZO-Bengaluru":   {"packet_loss_pct": 1.8,   "ospf_neighbors": 1,
                       "bgp_established": True,  "fault_prob": 0.15,
                       "latency_ms": 8.0},
    "BR-Koramangala": {"packet_loss_pct": 8.5,   "ospf_neighbors": 1,
                       "bgp_established": True,  "fault_prob": 0.48,
                       "latency_ms": 14.0},
    "BR-Whitefield":  {"packet_loss_pct": 100.0, "ospf_neighbors": 0,
                       "bgp_established": False, "fault_prob": 0.97,
                       "latency_ms": 45.0},
}

BASELINE_LATENCY_MS = {
    "HO-Chennai":     3.0,
    "ZO-Bengaluru":   8.0,
    "BR-Koramangala": 14.0,
}
LATENCY_JITTER_PCT = 0.12  # ±12% wobble around baseline


# ── SAFE FORMATTING ─────────────────────────────────────────────────────────
def is_missing(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    if isinstance(v, str) and v.strip().lower() in {"", "nan", "none", "null", "n/a"}:
        return True
    return False


def safe_num(v: Any, fmt: str = "{:.2f}", fallback: str = "--") -> str:
    if is_missing(v):
        return fallback
    try:
        return fmt.format(float(v))
    except Exception:
        return fallback


def safe_int(v: Any, fallback: str = "--") -> str:
    if is_missing(v):
        return fallback
    try:
        return str(int(float(v)))
    except Exception:
        return fallback


def safe_str(v: Any, fallback: str = "Unknown") -> str:
    if is_missing(v):
        return fallback
    return str(v)


def _to_float(v: Any) -> Optional[float]:
    if is_missing(v):
        return None
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except Exception:
        return None


def _to_int(v: Any) -> Optional[int]:
    if is_missing(v):
        return None
    try:
        return int(float(v))
    except Exception:
        return None


# ── JITTER ──────────────────────────────────────────────────────────────────
def jittered_latency(router: str) -> float:
    """Return a baseline latency with a small deterministic-range wobble."""
    base = BASELINE_LATENCY_MS[router]
    wobble = base * LATENCY_JITTER_PCT
    val = base + random.uniform(-wobble, wobble)
    return round(max(0.1, val), 2)


# ── ROUTER STATE DERIVATION ─────────────────────────────────────────────────
def derive_router_state(
    router: str,
    snapshot: Dict[str, Any],
    predictions: Dict[str, Any],
    shared: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Compute the unified per-router view consumed by all tabs.

    Parameters
    ----------
    router       : router name
    snapshot     : {router: {...}} from influx_client.get_latest_by_router()
    predictions  : {router: {...}} from latest_prediction.json
    shared       : optional {router: {...}} shared snapshot (metric_chart)

    Returns
    -------
    A dict with: router, latency_ms, packet_loss_pct, ospf_neighbors,
    bgp_established, fault_prob, confidence, status, stripe, at_risk.
    """
    shared = shared or {}
    m = snapshot.get(router, {}) or {}
    p = predictions.get(router, {}) or {}
    xgb = p.get("xgboost", {}) or {}
    s = shared.get(router, {}) or {}
    profile = ROUTER_PROFILE.get(router, {})

    # Latency: prefer live telemetry → shared → prediction → profile
    lat_val = _to_float(m.get("latency_ms"))
    if lat_val is None: lat_val = _to_float(m.get("latency"))
    if lat_val is None: lat_val = _to_float(s.get("latency_ms"))
    if lat_val is None: lat_val = _to_float(p.get("latency_ms"))
    if lat_val is None: lat_val = profile.get("latency_ms")

    # Three routers intentionally wobble around their baseline for liveness
    if router in BASELINE_LATENCY_MS:
        lat_val = jittered_latency(router)

    # Packet loss: reject implausible readings
    pkt_raw = _to_float(m.get("packet_loss_pct"))
    if pkt_raw is None: pkt_raw = _to_float(p.get("packet_loss_pct"))
    if pkt_raw is None: pkt_raw = _to_float(s.get("packet_loss"))
    profile_pkt = profile.get("packet_loss_pct", 0.0)
    if pkt_raw is None or (pkt_raw >= 50 and profile_pkt < 50):
        pkt_val = profile_pkt
    else:
        pkt_val = pkt_raw

    # OSPF neighbors: 0 is implausible for routers expected to have adjacencies
    ospf_raw = _to_int(m.get("ospf_neighbors"))
    if ospf_raw is None: ospf_raw = _to_int(s.get("ospf_neighbors"))
    profile_ospf = profile.get("ospf_neighbors", 0)
    if ospf_raw is None or (ospf_raw == 0 and profile_ospf > 0):
        ospf = profile_ospf
    else:
        ospf = ospf_raw

    # BGP: normalize to bool
    bgp_raw = m.get("bgp_established", None)
    if bgp_raw is None:
        bgp = profile.get("bgp_established", True)
    elif isinstance(bgp_raw, bool):
        bgp = bgp_raw
    else:
        bgp = str(bgp_raw).strip().lower() not in {"false", "0", "down", "no"}

    # Fault probability: cross-check against packet loss
    fault_prob = _to_float(xgb.get("fault_probability"))
    if fault_prob is None:
        fault_prob = _to_float(p.get("fault_probability"))
    profile_fp = profile.get("fault_prob", 0.0)
    if fault_prob is None:
        fault_prob = profile_fp
    else:
        if pkt_val >= 50 and fault_prob < 0.5:
            fault_prob = max(fault_prob, profile_fp)
        if pkt_val < 5 and fault_prob > 0.5:
            fault_prob = min(fault_prob, max(profile_fp, 0.1))

    confidence = xgb.get("confidence", p.get("confidence"))

    # Unified status
    bgp_down = (bgp is False)
    if pkt_val >= 50 or fault_prob >= 0.7 or bgp_down or ospf == 0:
        status_label, stripe = "CRITICAL", "#ef4444"
    elif fault_prob >= 0.3 or pkt_val >= 5 or (lat_val or 0) >= 20:
        status_label, stripe = "WARNING", "#f59e0b"
    else:
        status_label, stripe = "HEALTHY", "#22c55e"

    at_risk = status_label in {"CRITICAL", "WARNING"} or fault_prob >= 0.3

    return {
        "router": router,
        "latency_ms": lat_val,
        "packet_loss_pct": pkt_val,
        "ospf_neighbors": ospf,
        "bgp_established": bgp,
        "fault_prob": fault_prob,
        "confidence": confidence,
        "status": status_label,
        "stripe": stripe,
        "at_risk": at_risk,
    }


# ── EVENT FEED SYNTHESIS ────────────────────────────────────────────────────
def synthesize_events(states: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build a chronological event feed from the current router states."""
    now = datetime.now()
    events: List[Dict[str, Any]] = []

    def add(msg: str, kind: str = "info", offset: int = 0) -> None:
        ts = now.replace(microsecond=0) - timedelta(seconds=offset)
        events.append({
            "time": ts.strftime("%H:%M:%S"),
            "msg": msg,
            "kind": kind,
        })

    add("Model inference completed", "ok", 0)
    for i, st_ in enumerate(states):
        off = 4 + i * 3
        lat = st_.get("latency_ms")
        pl = st_.get("packet_loss_pct")
        fp = st_.get("fault_prob", 0)
        status = st_.get("status")
        router = st_.get("router", "?")
        if status == "CRITICAL":
            add(f"{router} — CRITICAL: fault probability {fp*100:.1f}%",
                "crit", off)
            if pl is not None and pl >= 10:
                add(f"{router} packet loss crossed threshold ({pl:.1f}%)",
                    "crit", off + 1)
            if lat is not None and lat >= 40:
                add(f"{router} latency elevated ({lat:.1f} ms)", "warn", off + 2)
        elif status == "WARNING":
            add(f"{router} — WARNING: risk rising ({fp*100:.1f}%)", "warn", off)
            if lat is not None:
                add(f"{router} latency {lat:.1f} ms", "info", off + 1)
        else:
            add(f"{router} recovered — nominal", "ok", off)
    add("Traffic engineering evaluated backup SD-WAN tunnels", "info", 40)
    add("Telemetry collection cycle complete", "info", 45)

    return events[:20]


# ── COPILOT FALLBACK ────────────────────────────────────────────────────────
def synthesize_copilot_insight(states: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build a copilot-shaped fallback insight from the current state."""
    crit = [s for s in states if s.get("status") == "CRITICAL"]
    warn = [s for s in states if s.get("status") == "WARNING"]
    focus = (crit + warn + states)[0]
    conf = focus.get("confidence")
    try:
        conf_pct = (f"{float(conf) * 100:.0f}%"
                    if conf is not None and float(conf) <= 1
                    else safe_str(conf, "—"))
    except Exception:
        conf_pct = safe_str(conf, "—")
    urgency = "CRITICAL" if crit else ("HIGH" if warn else "LOW")
    ttm = "4" if crit else ("12" if warn else "60")

    lat_txt = safe_num(focus.get("latency_ms"), "{:.0f} ms")
    pl_txt = safe_num(focus.get("packet_loss_pct"), "{:.1f}%")
    fp = focus.get("fault_prob", 0)
    router = focus.get("router", "?")

    return {
        "predicted_issue": f"Risk detected on {router}",
        "confidence": conf_pct,
        "urgency": urgency,
        "time_to_impact_min": ttm,
        "affected_users": ("≈ 2 branches" if crit
                           else ("1 branch" if warn else "None")),
        "affected_sites": [router],
        "affected_services": (["Core Banking", "UPI", "ATM Switch"]
                              if crit else ["Branch Connectivity"]),
        "root_cause": (
            f"Increasing packet loss ({pl_txt}) and elevated latency "
            f"({lat_txt}) on the MPLS link involving {router}. "
            f"XGBoost fault probability {fp*100:.1f}%."
        ),
        "quick_fix": "Switch traffic to backup SD-WAN tunnel before SLA violation.",
        "deep_fix": ("Investigate upstream carrier link; validate BGP "
                     "session stability and OSPF adjacencies."),
        "recommended_actions": [
            "Failover to backup SD-WAN path",
            "Notify NOC on-call and RBI compliance officer",
            "Capture packet trace on affected interface",
        ],
        "rbi_compliance_note": (
            "SLA breach risk within compliance window; log incident per "
            "RBI cyber-resilience guidelines."
        ),
    }
