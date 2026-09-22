#!/usr/bin/env python3
"""
Netwroxia — Phase B1: Network Health Score (v2)

Computes a 0-100 health score per router, per region, and for the whole
network. Blends three trusted sources with the following precedence rules:

  1. A4 beacon health (beacons/latest_beacon_health.json) — AUTHORITATIVE
     Direct live measurement. Wins over ML on disagreements about the
     current state.

  2. Stage 3 ML predictions (ml/inference/latest_prediction.json) — ADVISORY
     Forecast of future risk. Bounded by the beacon's credibility range.

  3. B2 service map (impact/service_map.json)
     Used for region rollup and for identifying receiver routers.

Design rules:
  - HO-Chennai is a beacon RECEIVER (never a sender). Its status is
    inferred from senders: if any sender reports OK, HO is up.
  - packet_loss comes from the BEACON (real measurement), not the
    prediction's cached raw_metrics (which can be stale).
  - Credible floor: if beacon says OK, ML alone can't drop score below 60.
    If DEGRADED, ML alone can't drop below 30.
  - BGP state comes from prediction's raw_metrics (beacon doesn't track BGP).
  - ml_mismatch flag is set when beacon and ML strongly disagree.

Output: analytics/latest_health.json

CLI:
    python3 analytics/health_score.py
    python3 analytics/health_score.py --show
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── CONFIG ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent

BEACON_HEALTH_PATH = PROJECT_ROOT / "beacons" / "latest_beacon_health.json"
PREDICTION_PATH = PROJECT_ROOT / "ml" / "inference" / "latest_prediction.json"
SERVICE_MAP_PATH = PROJECT_ROOT / "impact" / "service_map.json"
OUTPUT_PATH = PROJECT_ROOT / "analytics" / "latest_health.json"

BEACON_PENALTY = {
    "OK": 0,
    "DEGRADED": 15,
    "CRITICAL": 40,
    "MISSING": 70,
    "UNKNOWN": 20,
}

# Credible floor — ML alone cannot drop score below these values
CREDIBLE_FLOOR = {
    "OK": 60,
    "DEGRADED": 30,
    "CRITICAL": 0,
    "MISSING": 0,
    "UNKNOWN": 0,
}

XGBOOST_WEIGHT = 30.0
LSTM_WEIGHT = 20.0

ML_MISMATCH_THRESHOLD = 0.70   # beacon OK + ML > this = flagged mismatch

STATUS_THRESHOLDS = [
    (85, "HEALTHY"),
    (60, "WARNING"),
    (30, "CRITICAL"),
    (0,  "DOWN"),
]


# ── HELPERS ─────────────────────────────────────────────────────────────────
def load_json(path: Path, required: bool = True) -> Optional[Dict[str, Any]]:
    if not path.exists():
        if required:
            print(f"[FATAL] Missing required file: {path}")
            sys.exit(1)
        print(f"[WARN] Missing optional file: {path}")
        return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"[FATAL] Malformed JSON in {path}: {e}")
        sys.exit(1)


def save_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)


def score_to_status(score: float) -> str:
    for threshold, label in STATUS_THRESHOLDS:
        if score >= threshold:
            return label
    return "DOWN"


def clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


# ── INPUT LOADING ───────────────────────────────────────────────────────────
def load_beacon_map() -> Dict[str, Dict[str, Any]]:
    data = load_json(BEACON_HEALTH_PATH, required=False)
    if not data:
        return {}
    return data.get("routers", {})


def load_prediction_map() -> Dict[str, Dict[str, Any]]:
    data = load_json(PREDICTION_PATH, required=False)
    if not data:
        return {}
    predictions = data.get("predictions", [])
    return {p["router"]: p for p in predictions if "router" in p}


def load_service_map() -> Dict[str, Any]:
    return load_json(SERVICE_MAP_PATH, required=True)


def infer_receiver_beacon(
    router_name: str,
    beacon_map: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """
    HO-Chennai is a beacon receiver, not a sender. Infer its status from
    the senders: if any sender has OK status, HO is up (they reached it).
    """
    if not beacon_map:
        return {"status": "UNKNOWN", "score": 0, "loss_pct": 0.0, "inferred": True}

    statuses = [e.get("status", "UNKNOWN") for e in beacon_map.values()]

    if any(s == "OK" for s in statuses):
        return {"status": "OK", "score": 100, "loss_pct": 0.0, "inferred": True}
    if any(s == "DEGRADED" for s in statuses):
        return {"status": "DEGRADED", "score": 70, "loss_pct": 0.0, "inferred": True}
    if any(s == "CRITICAL" for s in statuses):
        return {"status": "CRITICAL", "score": 30, "loss_pct": 0.0, "inferred": True}
    return {"status": "MISSING", "score": 0, "loss_pct": 100.0, "inferred": True}


# ── CORE SCORING ────────────────────────────────────────────────────────────
def compute_router_score(
    router: str,
    beacon: Optional[Dict[str, Any]],
    prediction: Optional[Dict[str, Any]],
    is_receiver: bool,
    all_beacon_entries: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    # ── Beacon component ───────────────────────────────────────────────
    if beacon is None and is_receiver:
        beacon = infer_receiver_beacon(router, all_beacon_entries)

    if beacon:
        beacon_status = beacon.get("status", "UNKNOWN")
        beacon_score = int(beacon.get("score", 0))
        beacon_loss = float(beacon.get("loss_pct", 0.0) or 0.0)
    else:
        beacon_status = "UNKNOWN"
        beacon_score = 0
        beacon_loss = 0.0
    beacon_penalty = BEACON_PENALTY.get(beacon_status, 20)

    # ── ML component (advisory) ────────────────────────────────────────
    xgb_prob = 0.0
    lstm_prob = 0.0
    bgp_ok = True

    if prediction:
        xgb = prediction.get("xgboost", {}) or {}
        lstm = prediction.get("lstm_forecast", {}) or {}
        raw = prediction.get("raw_metrics", {}) or {}
        xgb_prob = float(xgb.get("fault_probability", 0.0) or 0.0)
        lstm_prob = float(lstm.get("future_fault_probability", 0.0) or 0.0)
        bgp_ok = bool(raw.get("bgp_established", True))

    xgb_penalty = xgb_prob * XGBOOST_WEIGHT
    lstm_penalty = lstm_prob * LSTM_WEIGHT
    ml_penalty = xgb_penalty + lstm_penalty

    # ── Combine penalties ──────────────────────────────────────────────
    score_before_floor = 100.0 - beacon_penalty - ml_penalty

    # ── Credible floor ─────────────────────────────────────────────────
    floor = CREDIBLE_FLOOR.get(beacon_status, 0)
    score_after_floor = max(score_before_floor, floor)
    floor_applied = score_after_floor > score_before_floor

    # ── Safety caps (from BEACON loss, not prediction) ─────────────────
    safety_cap_applied = None
    score = score_after_floor
    if beacon_loss >= 100.0:
        score = min(score, 5.0)
        safety_cap_applied = "beacon_loss_100"
    elif beacon_loss > 50.0:
        score = min(score, 20.0)
        safety_cap_applied = "beacon_loss_gt50"

    if not bgp_ok:
        score = min(score, 30.0)
        safety_cap_applied = safety_cap_applied or "bgp_down"

    score = clamp(score, 0.0, 100.0)

    # ── ML mismatch flag ───────────────────────────────────────────────
    ml_mismatch = (
        beacon_status == "OK"
        and (xgb_prob > ML_MISMATCH_THRESHOLD or lstm_prob > ML_MISMATCH_THRESHOLD)
    )

    return {
        "router": router,
        "score": round(score, 1),
        "status": score_to_status(score),
        "components": {
            "beacon_status": beacon_status,
            "beacon_score": beacon_score,
            "beacon_penalty": beacon_penalty,
            "beacon_loss_pct": round(beacon_loss, 2),
            "beacon_inferred": bool(beacon.get("inferred", False)) if beacon else False,
            "xgboost_fault_prob": round(xgb_prob, 4),
            "xgboost_penalty": round(xgb_penalty, 2),
            "lstm_future_prob": round(lstm_prob, 4),
            "lstm_penalty": round(lstm_penalty, 2),
            "ml_total_penalty": round(ml_penalty, 2),
            "credible_floor": floor,
            "credible_floor_applied": floor_applied,
            "bgp_established": bgp_ok,
            "safety_cap_applied": safety_cap_applied,
            "ml_mismatch": ml_mismatch,
        },
    }


def compute_region_score(
    region_name: str,
    router_scores: Dict[str, Dict[str, Any]],
    service_map: Dict[str, Any],
) -> Dict[str, Any]:
    members = [
        name for name, cfg in service_map["routers"].items()
        if cfg.get("region") == region_name
    ]
    if not members:
        return {"score": 0.0, "status": "DOWN",
                "router_count": 0, "user_count": 0, "routers": []}

    total_weight = 0.0
    weighted_sum = 0.0
    total_users = 0

    for name in members:
        if name not in router_scores:
            continue
        cfg = service_map["routers"][name]
        w = float(cfg.get("user_count", 0))
        s = float(router_scores[name]["score"])
        total_weight += w
        weighted_sum += w * s
        total_users += int(cfg.get("user_count", 0))

    if total_weight > 0.0:
        region_score = weighted_sum / total_weight
    else:
        vals = [router_scores[n]["score"] for n in members if n in router_scores]
        region_score = sum(vals) / len(vals) if vals else 0.0

    return {
        "score": round(region_score, 1),
        "status": score_to_status(region_score),
        "router_count": len(members),
        "user_count": total_users,
        "routers": members,
    }


# ── ORCHESTRATION ───────────────────────────────────────────────────────────
def build_health_report() -> Dict[str, Any]:
    beacon_map = load_beacon_map()
    prediction_map = load_prediction_map()
    service_map = load_service_map()

    routers_out: Dict[str, Dict[str, Any]] = {}

    for router_name, cfg in service_map["routers"].items():
        beacon = beacon_map.get(router_name)
        prediction = prediction_map.get(router_name)
        is_receiver = cfg.get("role") == "head_office"

        entry = compute_router_score(
            router_name, beacon, prediction, is_receiver, beacon_map
        )
        entry["region"] = cfg.get("region", "unknown")
        entry["role"] = cfg.get("role", "unknown")
        entry["user_count"] = int(cfg.get("user_count", 0))
        routers_out[router_name] = entry

    regions_out: Dict[str, Dict[str, Any]] = {}
    for region_name in service_map["regions"].keys():
        regions_out[region_name] = compute_region_score(
            region_name, routers_out, service_map
        )

    region_scores = [r["score"] for r in regions_out.values() if r["router_count"] > 0]
    network_score = sum(region_scores) / len(region_scores) if region_scores else 0.0

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source_files": {
            "beacon_health": str(BEACON_HEALTH_PATH.relative_to(PROJECT_ROOT)),
            "prediction": str(PREDICTION_PATH.relative_to(PROJECT_ROOT)),
            "service_map": str(SERVICE_MAP_PATH.relative_to(PROJECT_ROOT)),
        },
        "network": {
            "score": round(network_score, 1),
            "status": score_to_status(network_score),
            "router_count": len(routers_out),
            "region_count": len(regions_out),
        },
        "regions": regions_out,
        "routers": routers_out,
    }


# ── DISPLAY ─────────────────────────────────────────────────────────────────
def print_summary(report: Dict[str, Any]) -> None:
    net = report["network"]
    print("─" * 78)
    print(f" NETWORK SCORE : {net['score']:5.1f} / 100   [{net['status']}]")
    print(f" Routers       : {net['router_count']}")
    print(f" Regions       : {net['region_count']}")
    print("─" * 78)

    for rname, entry in report["routers"].items():
        c = entry["components"]
        flags = []
        if c["beacon_inferred"]:
            flags.append("INFERRED")
        if c["credible_floor_applied"]:
            flags.append(f"FLOOR={c['credible_floor']}")
        if c["safety_cap_applied"]:
            flags.append(f"CAP={c['safety_cap_applied']}")
        if c["ml_mismatch"]:
            flags.append("ML_MISMATCH")
        flag_str = f"  [{' '.join(flags)}]" if flags else ""

        print(
            f" {rname:18s} {entry['score']:5.1f}  "
            f"[{entry['status']:8s}]  "
            f"beacon={c['beacon_status']:8s}(-{c['beacon_penalty']:2d})  "
            f"xgb={c['xgboost_fault_prob']:.3f}(-{c['xgboost_penalty']:4.1f})  "
            f"lstm={c['lstm_future_prob']:.3f}(-{c['lstm_penalty']:4.1f})  "
            f"ml={c['ml_total_penalty']:5.1f}"
            f"{flag_str}"
        )

    print("─" * 78)
    for reg, data in report["regions"].items():
        print(
            f" Region {reg:12s} {data['score']:5.1f}  "
            f"[{data['status']:8s}]  "
            f"routers={data['router_count']}  users={data['user_count']}"
        )
    print("─" * 78)


# ── MAIN ────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="Netwroxia Health Score (B1 v2)")
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()

    print("=" * 78)
    print(" NETWROXIA HEALTH SCORE (B1 v2)")
    print("=" * 78)
    print(f" Beacon health : {BEACON_HEALTH_PATH.relative_to(PROJECT_ROOT)}")
    print(f" Prediction    : {PREDICTION_PATH.relative_to(PROJECT_ROOT)}")
    print(f" Service map   : {SERVICE_MAP_PATH.relative_to(PROJECT_ROOT)}")
    print(f" Output        : {OUTPUT_PATH.relative_to(PROJECT_ROOT)}")
    print("=" * 78)

    report = build_health_report()
    save_json(OUTPUT_PATH, report)

    if args.show:
        print_summary(report)
    else:
        print(
            f"[INFO] Network score: {report['network']['score']:.1f} "
            f"[{report['network']['status']}]"
        )

    print(f"[INFO] Report written -> {OUTPUT_PATH.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()