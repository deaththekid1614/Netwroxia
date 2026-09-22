#!/usr/bin/env python3
"""
Netwroxia — Phase B4: Business Impact Estimator (INR)

Converts B3's impact report into financial terms: loss rate per minute,
projected losses at 15/30/60/120 min, RBI compliance risk with penalty
estimates, category breakdown, and value of Netwroxia's early warning.

Output: impact/latest_business_impact.json

Design:
  - loss_rate_per_min = B3.total_revenue_per_min_inr (no double-counting)
  - category_breakdown groups affected_services by criticality (informational)
  - RBI penalties modelled per criticality tier
  - Breach thresholds: critical=30min, high=60min, medium=120min
  - Value of early warning = loss_rate * 5 min (Netwroxia's lead time)

CLI:
    python3 impact/business_impact.py
    python3 impact/business_impact.py --show
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── CONFIG ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent

IMPACT_PATH = PROJECT_ROOT / "impact" / "latest_impact.json"
SERVICE_MAP_PATH = PROJECT_ROOT / "impact" / "service_map.json"
OUTPUT_PATH = PROJECT_ROOT / "impact" / "latest_business_impact.json"

LEAD_TIME_MIN = 5  # Netwroxia's early-warning claim

# Projection horizons (minutes)
PROJECTION_HORIZONS = [15, 30, 60, 120]

# Tier thresholds (INR per minute)
TIER_THRESHOLDS = [
    ("CATASTROPHIC", 5_000_000),
    ("SEVERE",       1_000_000),
    ("MAJOR",          100_000),
    ("MINOR",                1),
    ("NEGLIGIBLE",           0),
]

# RBI breach thresholds per criticality (minutes)
RBI_BREACH_MIN = {
    "critical": 30,
    "high": 60,
    "medium": 120,
    "low": 240,
}

# RBI penalty base per breached service (INR)
RBI_PENALTY_BASE = {
    "critical": 500_000,
    "high": 200_000,
    "medium": 50_000,
    "low": 10_000,
}

# RBI penalty growth per hour after breach
RBI_PENALTY_PER_HOUR = {
    "critical": 100_000,
    "high": 50_000,
    "medium": 10_000,
    "low": 2_000,
}


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


def format_inr(n: float) -> str:
    """Format INR with Lakh/Crore units for readability."""
    if n is None:
        return "₹0"
    n = float(n)
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


def classify_tier(loss_per_min: float) -> str:
    for label, threshold in TIER_THRESHOLDS:
        if loss_per_min >= threshold:
            return label
    return "NEGLIGIBLE"


# ── CORE COMPUTATIONS ───────────────────────────────────────────────────────
def compute_current_loss(impact: Dict[str, Any]) -> Dict[str, Any]:
    per_min = float(impact["overall_impact"].get("total_revenue_per_min_inr", 0))
    per_hour = per_min * 60
    tier = classify_tier(per_min)
    return {
        "per_minute_inr": int(per_min),
        "per_minute_display": format_inr(per_min),
        "per_hour_inr": int(per_hour),
        "per_hour_display": format_inr(per_hour),
        "tier": tier,
        "affected_service_count": int(
            impact["overall_impact"].get("affected_service_count", 0)
        ),
    }


def compute_projections(loss_per_min: float) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for horizon in PROJECTION_HORIZONS:
        amount = loss_per_min * horizon
        out[f"min_{horizon}"] = {
            "minutes": horizon,
            "inr": int(amount),
            "display": format_inr(amount),
        }
    return out


def compute_category_breakdown(impact: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Group affected services by criticality tier."""
    buckets: Dict[str, Dict[str, Any]] = {
        "critical": {"service_count": 0, "down": 0, "degraded": 0, "service_revenue_per_min_inr": 0},
        "high":     {"service_count": 0, "down": 0, "degraded": 0, "service_revenue_per_min_inr": 0},
        "medium":   {"service_count": 0, "down": 0, "degraded": 0, "service_revenue_per_min_inr": 0},
        "low":      {"service_count": 0, "down": 0, "degraded": 0, "service_revenue_per_min_inr": 0},
    }

    for svc in impact.get("affected_services", []):
        crit = svc.get("criticality", "medium")
        if crit not in buckets:
            crit = "medium"
        buckets[crit]["service_count"] += 1
        if svc.get("availability") == "DOWN":
            buckets[crit]["down"] += 1
        else:
            buckets[crit]["degraded"] += 1
        buckets[crit]["service_revenue_per_min_inr"] += int(
            svc.get("revenue_per_min_inr", 0)
        )

    out: List[Dict[str, Any]] = []
    for crit in ["critical", "high", "medium", "low"]:
        b = buckets[crit]
        if b["service_count"] == 0:
            continue
        out.append({
            "category": crit,
            "service_count": b["service_count"],
            "services_down": b["down"],
            "services_degraded": b["degraded"],
            "service_revenue_per_min_inr": b["service_revenue_per_min_inr"],
            "service_revenue_display": format_inr(b["service_revenue_per_min_inr"]),
        })
    return out


def compute_rbi_compliance(impact: Dict[str, Any]) -> Dict[str, Any]:
    """
    For each affected RBI-mandated service, compute minutes-to-breach based
    on its criticality tier. Sum base penalties for breached services.
    """
    affected = impact.get("affected_services", [])
    rbi_services = [s for s in affected if s.get("rbi_mandated")]

    if not rbi_services:
        return {
            "applies": False,
            "breach_thresholds_min": dict(RBI_BREACH_MIN),
            "projected_breaches": [],
            "estimated_penalty_inr": 0,
            "estimated_penalty_display": "₹0",
            "narrative": "No RBI-mandated services affected. No compliance risk.",
        }

    projected = []
    penalty_sum = 0
    breached_count = 0

    for svc in rbi_services:
        crit = svc.get("criticality", "medium")
        availability = svc.get("availability", "UP")
        threshold = RBI_BREACH_MIN.get(crit, 120)

        # DOWN services breach at their threshold; DEGRADED breach at 2x
        if availability == "DOWN":
            minutes_to_breach = threshold
            breach_at_threshold = True
        else:  # DEGRADED
            minutes_to_breach = threshold * 2
            breach_at_threshold = False

        base = RBI_PENALTY_BASE.get(crit, 50_000)
        penalty_sum += base
        if breach_at_threshold:
            breached_count += 1

        projected.append({
            "service_id": svc.get("service_id", "unknown"),
            "name": svc.get("name", svc.get("service_id", "unknown")),
            "criticality": crit,
            "availability": availability,
            "minutes_to_breach": minutes_to_breach,
            "base_penalty_inr": base,
        })

    # Sort by soonest breach
    projected.sort(key=lambda s: s["minutes_to_breach"])

    if breached_count > 0:
        narrative = (
            f"{breached_count} RBI-mandated critical service(s) at DOWN state. "
            f"Compliance breach projected within {projected[0]['minutes_to_breach']} minutes "
            f"if unrecovered. Estimated penalty exposure: "
            f"{format_inr(penalty_sum)}."
        )
    else:
        narrative = (
            f"{len(rbi_services)} RBI-mandated service(s) DEGRADED. "
            f"Compliance breach projected within {projected[0]['minutes_to_breach']} minutes "
            f"if unrecovered. Estimated penalty exposure: {format_inr(penalty_sum)}."
        )

    return {
        "applies": True,
        "breach_thresholds_min": dict(RBI_BREACH_MIN),
        "projected_breaches": projected,
        "estimated_penalty_inr": int(penalty_sum),
        "estimated_penalty_display": format_inr(penalty_sum),
        "narrative": narrative,
    }


def compute_early_warning_value(loss_per_min: float) -> Dict[str, Any]:
    prevented = loss_per_min * LEAD_TIME_MIN
    if prevented == 0:
        narrative = "No active incident. Early-warning value is 0."
    else:
        narrative = (
            f"Netwroxia's {LEAD_TIME_MIN}-minute early warning converts "
            f"a {format_inr(loss_per_min)}/min incident into "
            f"{format_inr(prevented)} of potentially prevented loss."
        )
    return {
        "lead_time_min": LEAD_TIME_MIN,
        "prevented_loss_inr": int(prevented),
        "prevented_loss_display": format_inr(prevented),
        "narrative": narrative,
    }


# ── ORCHESTRATION ───────────────────────────────────────────────────────────
def build_business_report() -> Dict[str, Any]:
    impact = load_json(IMPACT_PATH, required=True)
    # service_map loaded for future use; not strictly needed in this version
    _ = load_json(SERVICE_MAP_PATH, required=False)

    current_loss = compute_current_loss(impact)
    loss_per_min = current_loss["per_minute_inr"]

    projections = compute_projections(loss_per_min)
    categories = compute_category_breakdown(impact)
    rbi = compute_rbi_compliance(impact)
    early_warning = compute_early_warning_value(loss_per_min)

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source_files": {
            "impact": str(IMPACT_PATH.relative_to(PROJECT_ROOT)),
        },
        "source_impact_timestamp": impact.get("timestamp", ""),
        "source_severity": impact.get("overall_impact", {}).get("severity", "UNKNOWN"),
        "current_loss": current_loss,
        "projections": projections,
        "category_breakdown": categories,
        "rbi_compliance": rbi,
        "value_of_early_warning": early_warning,
    }


# ── DISPLAY ─────────────────────────────────────────────────────────────────
def print_summary(report: Dict[str, Any]) -> None:
    cl = report["current_loss"]
    print("─" * 78)
    print(f" SEVERITY         : {report['source_severity']}")
    print(f" IMPACT TIER      : {cl['tier']}")
    print(f" Loss rate        : {cl['per_minute_display']} / min   "
          f"({cl['per_hour_display']} / hour)")
    print(f" Affected services: {cl['affected_service_count']}")
    print("─" * 78)

    print(" PROJECTIONS IF UNRECOVERED")
    for key in [f"min_{h}" for h in PROJECTION_HORIZONS]:
        p = report["projections"][key]
        print(f"   +{p['minutes']:>3d} min : {p['display']}")
    print("─" * 78)

    if report["category_breakdown"]:
        print(" CATEGORY BREAKDOWN")
        for cat in report["category_breakdown"]:
            print(
                f"   {cat['category']:9s}  services={cat['service_count']:2d} "
                f"(down={cat['services_down']}, degraded={cat['services_degraded']})  "
                f"revenue={cat['service_revenue_display']}/min"
            )
        print("─" * 78)

    if report["rbi_compliance"]["applies"]:
        rbi = report["rbi_compliance"]
        print(" RBI COMPLIANCE")
        print(f"   Estimated penalty : {rbi['estimated_penalty_display']}")
        print(f"   Services at risk  : {len(rbi['projected_breaches'])}")
        for pb in rbi["projected_breaches"]:
            print(
                f"     [{pb['criticality']:8s}] {pb['service_id']:24s} "
                f"availability={pb['availability']:9s} "
                f"breach_in={pb['minutes_to_breach']:3d}min "
                f"base_penalty={format_inr(pb['base_penalty_inr'])}"
            )
        print("─" * 78)

    ew = report["value_of_early_warning"]
    print(f" VALUE OF EARLY WARNING")
    print(f"   Lead time       : {ew['lead_time_min']} min")
    print(f"   Prevented loss  : {ew['prevented_loss_display']}")
    print("─" * 78)


# ── MAIN ────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="Netwroxia Business Impact (B4)")
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()

    print("=" * 78)
    print(" NETWROXIA BUSINESS IMPACT ESTIMATOR (B4)")
    print("=" * 78)
    print(f" Impact input : {IMPACT_PATH.relative_to(PROJECT_ROOT)}")
    print(f" Output       : {OUTPUT_PATH.relative_to(PROJECT_ROOT)}")
    print("=" * 78)

    report = build_business_report()
    save_json(OUTPUT_PATH, report)

    if args.show:
        print_summary(report)
    else:
        cl = report["current_loss"]
        print(
            f"[INFO] Tier: {cl['tier']}  "
            f"loss={cl['per_minute_display']}/min  "
            f"RBI_penalty={report['rbi_compliance']['estimated_penalty_display']}"
        )

    print(f"[INFO] Report written -> {OUTPUT_PATH.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
