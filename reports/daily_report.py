#!/usr/bin/env python3
"""
Netwroxia — Phase D7: Daily Report

Consolidates D1–D6 + B1 into a single daily NOC snapshot. Every section
degrades gracefully if its source is unavailable.

Reads:
  analytics/latest_kpis.json          (D1)
  analytics/latest_sla.json           (D2)
  analytics/latest_trends.json        (D3)
  mlops/latest_registry.json          (D4)
  mlops/latest_drift.json             (D5)
  mlops/latest_performance.json       (D6)
  analytics/latest_health.json        (B1)

Writes:
  reports/daily/daily_YYYY-MM-DD.json    full structured snapshot
  reports/daily/latest.json              always overwritten

CLI:
    python3 reports/daily_report.py generate
    python3 reports/daily_report.py generate --show
    python3 reports/daily_report.py generate --date 2026-09-20
"""

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# ── PATH SETUP ──────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ── CONFIG ──────────────────────────────────────────────────────────────────
ANALYTICS_DIR = PROJECT_ROOT / "analytics"
MLOPS_DIR = PROJECT_ROOT / "mlops"
OUTPUT_DIR = PROJECT_ROOT / "reports" / "daily"

SOURCES = {
    "kpis":        ANALYTICS_DIR / "latest_kpis.json",
    "sla":         ANALYTICS_DIR / "latest_sla.json",
    "trends":      ANALYTICS_DIR / "latest_trends.json",
    "health":      ANALYTICS_DIR / "latest_health.json",
    "registry":    MLOPS_DIR / "latest_registry.json",
    "drift":       MLOPS_DIR / "latest_drift.json",
    "performance": MLOPS_DIR / "latest_performance.json",
}


# ── HELPERS ─────────────────────────────────────────────────────────────────
def _safe_relpath(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _missing(name: str, path: Path) -> Dict[str, Any]:
    return {
        "status": "unavailable",
        "reason": f"source not found: {_safe_relpath(path)}",
        "source": name,
    }


# ── SECTION BUILDERS ────────────────────────────────────────────────────────
def build_executive_section(
    health: Optional[Dict[str, Any]],
    sla: Optional[Dict[str, Any]],
    kpis: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Top-of-report summary: network status, SLA, KPIs at a glance."""
    out: Dict[str, Any] = {}

    if health:
        net = health.get("network", {})
        out["network_score"] = net.get("score")
        out["network_status"] = net.get("status")
        routers = health.get("routers", {})
        counts = {"HEALTHY": 0, "WARNING": 0, "CRITICAL": 0, "DOWN": 0}
        for r in routers.values():
            s = (r.get("status") or "UNKNOWN").upper()
            if s in counts:
                counts[s] += 1
        out["router_health_counts"] = counts
    else:
        out["network_status"] = "unavailable"

    if sla:
        ov = sla.get("overall", {})
        out["sla_compliance_rate"] = ov.get("compliance_rate")
        out["services_total"] = ov.get("total_services")
        out["services_at_risk"] = ov.get("at_risk_services", [])
        out["branches_at_risk"] = ov.get("at_risk_branches", [])

    if kpis:
        mttd = kpis.get("mttd", {})
        mttr = kpis.get("mttr", {})
        mtbf = kpis.get("mtbf", {})
        av = kpis.get("availability", {}).get("overall", {})
        out["mttd_min"] = mttd.get("mean_minutes")
        out["mttr_min"] = mttr.get("mean_minutes")
        out["mtbf_hours"] = mtbf.get("overall_mean_hours")
        out["availability_pct"] = av.get("availability_pct")

    return out


def build_incident_section(trends: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Today's incidents from the trends report (newest bucket)."""
    if not trends:
        return {"status": "unavailable"}
    buckets = trends.get("buckets") or []
    if not buckets:
        return {"incidents_total": 0, "by_severity": {}}
    latest = buckets[-1]
    return {
        "bucket_start": latest.get("start"),
        "bucket_end": latest.get("end"),
        "incidents_total": latest.get("incidents_total", 0),
        "by_severity": latest.get("incidents_by_severity", {}),
        "downtime_weighted_seconds": latest.get("downtime_weighted_seconds", 0),
        "unique_routers": latest.get("unique_routers", []),
        "trend_label": trends.get("summary", {}).get("trend_label"),
    }


def build_ml_section(
    registry: Optional[Dict[str, Any]],
    drift: Optional[Dict[str, Any]],
    performance: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    out: Dict[str, Any] = {}

    if registry:
        out["models_total"] = registry.get("total_models")
        out["currently_used"] = registry.get("currently_used", {})
        by_type = registry.get("by_type", {})
        out["model_types"] = {
            t: {"count": v.get("count"), "latest": v.get("latest_file")}
            for t, v in by_type.items()
        }
    else:
        out["models_total"] = None

    if drift:
        out["drift_status"] = drift.get("status", "unknown")
        ov = drift.get("overall", {})
        out["drift_score"] = ov.get("drift_score")
        out["drift_routers_flagged"] = [
            r["router"] for r in drift.get("routers", [])
            if r.get("status") in ("WATCH", "DRIFTED")
        ]
    else:
        out["drift_status"] = "unavailable"

    if performance and performance.get("status") == "ok":
        snap = performance.get("current_snapshot", {})
        m = snap.get("metrics", {})
        out["prediction_precision"] = m.get("precision")
        out["prediction_recall"] = m.get("recall")
        out["prediction_f1"] = m.get("f1")
        out["confusion"] = {
            "tp": m.get("tp"), "fp": m.get("fp"),
            "fn": m.get("fn"), "tn": m.get("tn"),
        }
    else:
        out["prediction_precision"] = None

    return out


# ── ORCHESTRATION ───────────────────────────────────────────────────────────
def build_daily_report(target_date: Optional[str] = None) -> Dict[str, Any]:
    """
    Build the daily report. `target_date` in YYYY-MM-DD; defaults to today
    in UTC.
    """
    generated_at = datetime.now(timezone.utc)
    if target_date is None:
        target_date = generated_at.strftime("%Y-%m-%d")

    loaded: Dict[str, Optional[Dict[str, Any]]] = {}
    for name, path in SOURCES.items():
        loaded[name] = _load_json(path)

    missing_sources = [
        {"name": n, "path": _safe_relpath(SOURCES[n])}
        for n, v in loaded.items() if v is None
    ]

    return {
        "report_type": "daily",
        "report_date": target_date,
        "generated_at": generated_at.isoformat(),
        "sources_status": {
            name: ("available" if v is not None else "missing")
            for name, v in loaded.items()
        },
        "missing_sources": missing_sources,
        "executive_summary": build_executive_section(
            loaded["health"], loaded["sla"], loaded["kpis"]
        ),
        "incidents_today": build_incident_section(loaded["trends"]),
        "ml_status": build_ml_section(
            loaded["registry"], loaded["drift"], loaded["performance"]
        ),
        "raw": {
            "kpis": loaded["kpis"],
            "sla": loaded["sla"],
            "trends_summary": (loaded["trends"] or {}).get("summary"),
            "performance": (loaded["performance"] or {}).get("current_snapshot"),
        },
    }


# ── DISPLAY ─────────────────────────────────────────────────────────────────
def print_summary(report: Dict[str, Any]) -> None:
    hr = "─" * 78
    ex = report["executive_summary"]
    inc = report["incidents_today"]
    ml = report["ml_status"]

    print(hr)
    print(f" NETWROXIA — DAILY REPORT  {report['report_date']}")
    print(f" generated_at = {report['generated_at']}")
    print(hr)

    print(" EXECUTIVE SUMMARY")
    print(f"   Network score     : {ex.get('network_score')} "
          f"[{ex.get('network_status')}]")
    rh = ex.get("router_health_counts") or {}
    if rh:
        print(f"   Routers           : "
              f"healthy={rh.get('HEALTHY',0)} "
              f"warn={rh.get('WARNING',0)} "
              f"crit={rh.get('CRITICAL',0)} "
              f"down={rh.get('DOWN',0)}")
    print(f"   SLA compliance    : {ex.get('sla_compliance_rate')}%  "
          f"({ex.get('services_total')} services)")
    if ex.get("services_at_risk"):
        print(f"   Services at risk  : {', '.join(ex['services_at_risk'])}")
    if ex.get("branches_at_risk"):
        print(f"   Branches at risk  : {', '.join(ex['branches_at_risk'])}")
    print(f"   MTTD              : {ex.get('mttd_min')} min")
    print(f"   MTTR              : {ex.get('mttr_min')} min")
    print(f"   MTBF              : {ex.get('mtbf_hours')} h")
    print(f"   Availability      : {ex.get('availability_pct')}%")
    print(hr)

    print(" INCIDENTS (newest bucket)")
    print(f"   Count             : {inc.get('incidents_total', 0)}")
    print(f"   By severity       : {inc.get('by_severity', {})}")
    print(f"   Downtime (weight) : {inc.get('downtime_weighted_seconds', 0)} s")
    print(f"   Unique routers    : {inc.get('unique_routers', [])}")
    print(f"   Trend             : {inc.get('trend_label')}")
    print(hr)

    print(" ML STATUS")
    print(f"   Models tracked    : {ml.get('models_total')}")
    cu = ml.get("currently_used", {})
    print(f"   Loaded XGB slot   : {cu.get('xgboost_slot')}")
    print(f"   Loaded LSTM slot  : {cu.get('lstm_slot')}")
    print(f"   Drift status      : {ml.get('drift_status')} "
          f"(score={ml.get('drift_score')})")
    if ml.get("drift_routers_flagged"):
        print(f"   Drift routers     : {', '.join(ml['drift_routers_flagged'])}")
    print(f"   Prediction P/R/F1 : "
          f"{ml.get('prediction_precision')} / "
          f"{ml.get('prediction_recall')} / "
          f"{ml.get('prediction_f1')}")
    conf = ml.get("confusion") or {}
    if conf:
        print(f"   Confusion         : "
              f"TP={conf.get('tp')} FP={conf.get('fp')} "
              f"FN={conf.get('fn')} TN={conf.get('tn')}")
    print(hr)

    if report["missing_sources"]:
        print(" MISSING SOURCES")
        for ms in report["missing_sources"]:
            print(f"   {ms['name']:12s}  {ms['path']}")
        print(hr)


# ── CLI ─────────────────────────────────────────────────────────────────────
def cmd_generate(args) -> int:
    report = build_daily_report(target_date=args.date)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    day_file = OUTPUT_DIR / f"daily_{report['report_date']}.json"
    latest_file = OUTPUT_DIR / "latest.json"

    with open(day_file, "w") as f:
        json.dump(report, f, indent=2, default=str)
    shutil.copy2(day_file, latest_file)

    if args.show:
        print_summary(report)
    else:
        print(f"[OK] daily report written to {_safe_relpath(day_file)}")
        print(f"[OK] latest copy updated: {_safe_relpath(latest_file)}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Netwroxia Daily Report (D7)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_gen = sub.add_parser("generate", help="Build the daily report")
    p_gen.add_argument("--show", action="store_true")
    p_gen.add_argument("--date", default=None,
                       help="YYYY-MM-DD (default: today UTC)")

    args = parser.parse_args()
    if args.command == "generate":
        sys.exit(cmd_generate(args))


if __name__ == "__main__":
    main()
