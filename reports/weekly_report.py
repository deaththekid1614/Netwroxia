#!/usr/bin/env python3
"""
Netwroxia — Phase D8: Weekly Report

Aggregates 7 days of daily reports (D7 output) into a week-level rollup.
Uses ISO weeks (Monday to Sunday). Compares to the previous week when
available.

Reads:
  reports/daily/daily_*.json            (D7 output)

Writes:
  reports/weekly/weekly_YYYY-WNN.json   full week snapshot
  reports/weekly/latest.json            always overwritten

CLI:
    python3 reports/weekly_report.py generate
    python3 reports/weekly_report.py generate --show
    python3 reports/weekly_report.py generate --end 2026-09-20
"""

import argparse
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# ── PATH SETUP ──────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ── CONFIG ──────────────────────────────────────────────────────────────────
DAILY_DIR = PROJECT_ROOT / "reports" / "daily"
OUTPUT_DIR = PROJECT_ROOT / "reports" / "weekly"

SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]


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


def _week_range(end_date: str) -> List[str]:
    """
    Given a YYYY-MM-DD date, return the 7-day list (Mon..Sun) of the ISO
    week containing it.
    """
    dt = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    # Monday of that ISO week
    monday = dt - timedelta(days=dt.weekday())
    return [(monday + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]


def _iso_week_label(any_day: str) -> str:
    dt = datetime.strptime(any_day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def _mean(values: List[float]) -> Optional[float]:
    values = [v for v in values if v is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 4)


# ── AGGREGATION ─────────────────────────────────────────────────────────────
def aggregate(days: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    Aggregate a dict of {date_str: daily_report_data}.
    Days may be a partial set; caller supplies only those present.
    """
    if not days:
        return {
            "incidents_total": 0,
            "incidents_by_severity": {s: 0 for s in SEVERITIES},
            "downtime_weighted_seconds_total": 0.0,
            "unique_routers_affected": [],
            "avg_network_score": None,
            "min_network_score": None,
            "max_network_score": None,
            "worst_day_by_score": None,
            "worst_day_by_incidents": None,
            "avg_sla_compliance_rate": None,
            "total_days": 0,
        }

    total_incidents = 0
    by_sev = {s: 0 for s in SEVERITIES}
    total_downtime = 0.0
    routers: Set[str] = set()
    scores: List[float] = []
    sla_rates: List[float] = []
    per_day: Dict[str, Dict[str, Any]] = {}

    for day, data in sorted(days.items()):
        inc = data.get("incidents_today", {})
        cnt = int(inc.get("incidents_total") or 0)
        total_incidents += cnt
        for s, v in (inc.get("by_severity") or {}).items():
            if s in by_sev:
                by_sev[s] += int(v or 0)
        total_downtime += float(inc.get("downtime_weighted_seconds") or 0.0)
        for r in (inc.get("unique_routers") or []):
            routers.add(r)

        ex = data.get("executive_summary", {})
        score = ex.get("network_score")
        if isinstance(score, (int, float)):
            scores.append(float(score))
        sla_rate = ex.get("sla_compliance_rate")
        if isinstance(sla_rate, (int, float)):
            sla_rates.append(float(sla_rate))

        per_day[day] = {
            "incidents": cnt,
            "network_score": score,
            "sla_compliance_rate": sla_rate,
        }

    worst_by_score = None
    if scores:
        # pick the day with lowest score (only days with a score)
        candidates = [
            (d, v["network_score"])
            for d, v in per_day.items()
            if isinstance(v["network_score"], (int, float))
        ]
        if candidates:
            worst_by_score = min(candidates, key=lambda x: x[1])[0]

    worst_by_inc = None
    if per_day:
        worst_by_inc = max(per_day.items(), key=lambda kv: kv[1]["incidents"])[0]

    return {
        "incidents_total": total_incidents,
        "incidents_by_severity": by_sev,
        "downtime_weighted_seconds_total": round(total_downtime, 2),
        "unique_routers_affected": sorted(routers),
        "avg_network_score": _mean(scores),
        "min_network_score": min(scores) if scores else None,
        "max_network_score": max(scores) if scores else None,
        "worst_day_by_score": worst_by_score,
        "worst_day_by_incidents": worst_by_inc,
        "avg_sla_compliance_rate": _mean(sla_rates),
        "total_days": len(days),
        "per_day": per_day,
    }


def compare_to_previous_week(
    this_week: Dict[str, Any],
    prev_week: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Simple directional comparison vs. previous week."""
    if not prev_week:
        return None

    def delta(a, b):
        if a is None or b is None:
            return None
        return round(a - b, 4)

    delta_incidents = delta(this_week.get("incidents_total"),
                            prev_week.get("incidents_total"))
    delta_score = delta(this_week.get("avg_network_score"),
                        prev_week.get("avg_network_score"))
    delta_downtime = delta(this_week.get("downtime_weighted_seconds_total"),
                           prev_week.get("downtime_weighted_seconds_total"))

    # Trend label from incidents (fewer = better = improving)
    if delta_incidents is None:
        trend = "no_data"
    elif delta_incidents < -2:
        trend = "improving"
    elif delta_incidents > 2:
        trend = "worsening"
    else:
        trend = "stable"

    return {
        "prev_week_label": prev_week.get("week_label"),
        "prev_incidents_total": prev_week.get("incidents_total"),
        "delta_incidents": delta_incidents,
        "delta_avg_network_score": delta_score,
        "delta_downtime_seconds": delta_downtime,
        "trend_label": trend,
    }


# ── ORCHESTRATION ───────────────────────────────────────────────────────────
def build_weekly_report(end_date: Optional[str] = None) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    if end_date is None:
        end_date = now.strftime("%Y-%m-%d")

    days = _week_range(end_date)
    week_label = _iso_week_label(end_date)

    loaded: Dict[str, Dict[str, Any]] = {}
    missing: List[str] = []
    for day in days:
        path = DAILY_DIR / f"daily_{day}.json"
        data = _load_json(path)
        if data is None:
            missing.append(day)
        else:
            loaded[day] = data

    aggregated = aggregate(loaded)

    # Previous week — best-effort
    prev_end = (datetime.strptime(days[0], "%Y-%m-%d")
                - timedelta(days=1)).strftime("%Y-%m-%d")
    prev_days = _week_range(prev_end)
    prev_loaded: Dict[str, Dict[str, Any]] = {}
    for day in prev_days:
        path = DAILY_DIR / f"daily_{day}.json"
        data = _load_json(path)
        if data is not None:
            prev_loaded[day] = data
    prev_aggregated = aggregate(prev_loaded) if prev_loaded else None
    if prev_aggregated is not None:
        prev_aggregated["week_label"] = _iso_week_label(prev_end)

    comparison = compare_to_previous_week(aggregated, prev_aggregated)

    return {
        "report_type": "weekly",
        "week_label": week_label,
        "week_start": days[0],
        "week_end": days[-1],
        "generated_at": now.isoformat(),
        "days_included": sorted(loaded.keys()),
        "days_missing": missing,
        "days_expected": days,
        "summary": {
            k: v for k, v in aggregated.items() if k != "per_day"
        },
        "per_day": aggregated.get("per_day", {}),
        "comparison_to_previous_week": comparison,
    }


# ── DISPLAY ─────────────────────────────────────────────────────────────────
def print_summary(report: Dict[str, Any]) -> None:
    hr = "─" * 78
    s = report["summary"]
    print(hr)
    print(f" NETWROXIA — WEEKLY REPORT  {report['week_label']}")
    print(f" week: {report['week_start']} → {report['week_end']}")
    print(f" generated_at = {report['generated_at']}")
    print(hr)

    print(f" Days included  : {len(report['days_included'])}/7")
    if report["days_missing"]:
        print(f" Days missing   : {', '.join(report['days_missing'])}")
    print(f" Incidents      : {s['incidents_total']}")
    print(f" By severity    : {s['incidents_by_severity']}")
    print(f" Downtime (w)   : {s['downtime_weighted_seconds_total']:.0f} s")
    print(f" Routers hit    : {s['unique_routers_affected']}")
    print(f" Avg net score  : {s['avg_network_score']}  "
          f"(min={s['min_network_score']}, max={s['max_network_score']})")
    print(f" Avg SLA rate   : {s['avg_sla_compliance_rate']}%")
    if s["worst_day_by_score"]:
        print(f" Worst by score : {s['worst_day_by_score']}")
    if s["worst_day_by_incidents"]:
        print(f" Worst by inc   : {s['worst_day_by_incidents']}")

    cmp = report.get("comparison_to_previous_week")
    if cmp:
        print(hr)
        print(f" COMPARISON vs {cmp['prev_week_label']}")
        print(f"   Delta incidents : {cmp['delta_incidents']}")
        print(f"   Delta avg score : {cmp['delta_avg_network_score']}")
        print(f"   Delta downtime  : {cmp['delta_downtime_seconds']} s")
        print(f"   Trend label     : {cmp['trend_label']}")
    else:
        print(hr)
        print(" (no previous week data for comparison)")

    print(hr)
    print(" PER-DAY")
    for day, v in sorted(report["per_day"].items()):
        print(f"   {day}  incidents={v['incidents']:>3}  "
              f"score={v['network_score']}  sla={v['sla_compliance_rate']}%")
    print(hr)


# ── CLI ─────────────────────────────────────────────────────────────────────
def cmd_generate(args) -> int:
    report = build_weekly_report(end_date=args.end)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    week_file = OUTPUT_DIR / f"weekly_{report['week_label']}.json"
    latest_file = OUTPUT_DIR / "latest.json"

    with open(week_file, "w") as f:
        json.dump(report, f, indent=2, default=str)
    shutil.copy2(week_file, latest_file)

    if args.show:
        print_summary(report)
    else:
        print(f"[OK] weekly report written to {_safe_relpath(week_file)}")
        print(f"[OK] latest copy updated: {_safe_relpath(latest_file)}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Netwroxia Weekly Report (D8)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_gen = sub.add_parser("generate", help="Build the weekly report")
    p_gen.add_argument("--show", action="store_true")
    p_gen.add_argument("--end", default=None,
                       help="YYYY-MM-DD in the target week (default: today)")

    args = parser.parse_args()
    if args.command == "generate":
        sys.exit(cmd_generate(args))


if __name__ == "__main__":
    main()
