#!/usr/bin/env python3
"""
Netwroxia — Phase D1: KPI Calculator

Computes core NOC KPIs from Netwroxia's persistent artifacts:

  MTTD  Mean Time To Detect     fault injection -> incident.created
  MTTR  Mean Time To Resolve    incident.created -> RESOLVED
  MTBF  Mean Time Between Failures  per-router, consecutive incidents
  Availability  uptime % from InfluxDB ping measurement

Output: analytics/latest_kpis.json

Sources:
  incidents/incidents.db                    (C2)
  fault_sim/active_faults.json              (A5)
  http://localhost:8086 ping measurement    (Stage 2)

Env overrides:
  NETWROXIA_DB_PATH             default: incidents/incidents.db

CLI:
    python3 analytics/kpi_calculator.py report
    python3 analytics/kpi_calculator.py report --show
    python3 analytics/kpi_calculator.py report --window 48
    python3 analytics/kpi_calculator.py report --no-influx
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

# ── PATH SETUP ──────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from incidents import incident_store as store

# ── CONFIG ──────────────────────────────────────────────────────────────────
FAULT_STATE_PATH = PROJECT_ROOT / "fault_sim" / "active_faults.json"
OUTPUT_PATH = PROJECT_ROOT / "analytics" / "latest_kpis.json"

INFLUX_URL = "http://localhost:8086"
INFLUX_DB = "netwroxia"
INFLUX_TIMEOUT = 10

DEFAULT_AVAILABILITY_WINDOW_HOURS = 24
LOSS_THRESHOLD_PCT = 5.0   # below this = considered "up"


# ── HELPERS ─────────────────────────────────────────────────────────────────
def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _load_fault_events() -> List[Dict[str, Any]]:
    """Read fault_sim/active_faults.json for injection timestamps."""
    if not FAULT_STATE_PATH.exists():
        return []
    try:
        with open(FAULT_STATE_PATH) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    events = data.get("active", [])
    out: List[Dict[str, Any]] = []
    for e in events:
        ts = _parse_iso(e.get("applied_at"))
        if ts is None:
            continue
        out.append({
            "scenario": e.get("scenario", "unknown"),
            "params": e.get("params", {}),
            "applied_at": ts,
        })
    out.sort(key=lambda x: x["applied_at"])
    return out


def _find_preceding_fault(
    incident_created: datetime,
    faults: List[Dict[str, Any]],
    max_lookback_min: int = 60,
) -> Optional[Dict[str, Any]]:
    """
    Find the most recent fault injected before `incident_created`
    within `max_lookback_min` minutes. Returns the fault dict or None.
    """
    cutoff = incident_created - timedelta(minutes=max_lookback_min)
    candidates = [f for f in faults if cutoff <= f["applied_at"] <= incident_created]
    return candidates[-1] if candidates else None


# ── INFLUX ──────────────────────────────────────────────────────────────────
def _query_ping_availability(window_hours: int) -> Dict[str, Any]:
    """
    Query InfluxDB for ping data over the window. Returns per-router
    availability stats and an overall figure.

    Returns:
      {
        "queried": bool,
        "window_hours": int,
        "per_router": {router: {samples, up_samples, availability_pct}},
        "overall": {samples, up_samples, availability_pct}
      }
    """
    since = (datetime.now(timezone.utc) - timedelta(hours=window_hours)) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    query = (
        f"SELECT time, url, percent_packet_loss FROM ping "
        f"WHERE time > '{since}'"
    )

    try:
        resp = requests.get(
            f"{INFLUX_URL}/query",
            params={"db": INFLUX_DB, "q": query},
            timeout=INFLUX_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return {
            "queried": False,
            "window_hours": window_hours,
            "per_router": {},
            "overall": {"samples": 0, "up_samples": 0, "availability_pct": 0.0},
        }

    series = (data.get("results") or [{}])[0].get("series", [])
    if not series:
        return {
            "queried": True,
            "window_hours": window_hours,
            "per_router": {},
            "overall": {"samples": 0, "up_samples": 0, "availability_pct": 0.0},
        }

    columns = series[0].get("columns", [])
    values = series[0].get("values", [])

    ip_map = {
        "172.20.20.2": "HO-Chennai",
        "172.20.20.3": "ZO-Bengaluru",
        "172.20.20.4": "BR-Koramangala",
        "172.20.20.5": "BR-Whitefield",
        "172.20.20.7": "BR-Whitefield",  # legacy
    }

    per_router: Dict[str, Dict[str, int]] = {}
    total_samples = 0
    total_up = 0

    for row in values:
        rec = dict(zip(columns, row))
        url = rec.get("url", "")
        router = ip_map.get(url, url or "unknown")
        loss = rec.get("percent_packet_loss")
        try:
            loss_f = float(loss)
        except (TypeError, ValueError):
            continue

        r = per_router.setdefault(router, {"samples": 0, "up_samples": 0})
        r["samples"] += 1
        total_samples += 1
        if loss_f < LOSS_THRESHOLD_PCT:
            r["up_samples"] += 1
            total_up += 1

    per_router_out: Dict[str, Dict[str, Any]] = {}
    for router, counts in per_router.items():
        s = counts["samples"]
        u = counts["up_samples"]
        pct = round((u / s) * 100.0, 3) if s > 0 else 0.0
        per_router_out[router] = {
            "samples": s,
            "up_samples": u,
            "availability_pct": pct,
        }

    overall_pct = round((total_up / total_samples) * 100.0, 3) \
        if total_samples > 0 else 0.0

    return {
        "queried": True,
        "window_hours": window_hours,
        "per_router": per_router_out,
        "overall": {
            "samples": total_samples,
            "up_samples": total_up,
            "availability_pct": overall_pct,
        },
    }


# ── KPI CALCULATIONS ────────────────────────────────────────────────────────
def calculate_mttd() -> Dict[str, Any]:
    """
    MTTD = time from fault injection to incident detection.
    For each incident, find the most recent fault injected within 60 min
    before the incident was created. Difference = MTTD for that incident.
    """
    faults = _load_fault_events()
    incidents = store.list_incidents(limit=1000)

    samples: List[Dict[str, Any]] = []
    for inc in incidents:
        created = _parse_iso(inc.get("created_at"))
        if created is None:
            continue
        fault = _find_preceding_fault(created, faults, max_lookback_min=60)
        if fault is None:
            continue
        delta_sec = (created - fault["applied_at"]).total_seconds()
        if delta_sec < 0 or delta_sec > 3600:
            continue
        samples.append({
            "incident_id": inc["incident_id"],
            "root_router": inc.get("root_router", ""),
            "fault_scenario": fault["scenario"],
            "seconds": round(delta_sec, 2),
        })

    if not samples:
        return {
            "mean_seconds": 0.0,
            "mean_minutes": 0.0,
            "sample_count": 0,
            "samples": [],
            "note": "no paired fault+incident observations",
        }

    mean_sec = sum(s["seconds"] for s in samples) / len(samples)
    return {
        "mean_seconds": round(mean_sec, 2),
        "mean_minutes": round(mean_sec / 60.0, 2),
        "sample_count": len(samples),
        "samples": samples,
    }


def calculate_mttr() -> Dict[str, Any]:
    """
    MTTR = time from incident creation to RESOLVED state.
    Uses resolved_at when present; falls back to updated_at only if
    the incident is in RESOLVED state.
    """
    incidents = store.list_incidents(limit=1000)

    samples: List[Dict[str, Any]] = []
    for inc in incidents:
        created = _parse_iso(inc.get("created_at"))
        resolved = _parse_iso(inc.get("resolved_at"))
        if created is None or resolved is None:
            continue
        delta_sec = (resolved - created).total_seconds()
        if delta_sec < 0:
            continue
        samples.append({
            "incident_id": inc["incident_id"],
            "root_router": inc.get("root_router", ""),
            "severity": inc.get("severity", ""),
            "seconds": round(delta_sec, 2),
        })

    if not samples:
        return {
            "mean_seconds": 0.0,
            "mean_minutes": 0.0,
            "sample_count": 0,
            "samples": [],
            "note": "no resolved incidents yet",
        }

    mean_sec = sum(s["seconds"] for s in samples) / len(samples)

    # Per-severity breakdown (informational)
    by_sev: Dict[str, List[float]] = {}
    for s in samples:
        by_sev.setdefault(s["severity"], []).append(s["seconds"])
    per_severity = {
        sev: round(sum(vals) / len(vals), 2)
        for sev, vals in by_sev.items()
    }

    return {
        "mean_seconds": round(mean_sec, 2),
        "mean_minutes": round(mean_sec / 60.0, 2),
        "sample_count": len(samples),
        "per_severity_seconds": per_severity,
        "samples": samples,
    }


def calculate_mtbf() -> Dict[str, Any]:
    """
    MTBF = average time between consecutive incidents on the same router.
    Computed per router, then averaged for network-wide figure.
    """
    incidents = store.list_incidents(limit=1000)

    by_router: Dict[str, List[datetime]] = {}
    for inc in incidents:
        created = _parse_iso(inc.get("created_at"))
        if created is None:
            continue
        router = inc.get("root_router") or "unknown"
        by_router.setdefault(router, []).append(created)

    per_router: Dict[str, Dict[str, Any]] = {}
    all_gaps: List[float] = []

    for router, timestamps in by_router.items():
        timestamps.sort()
        gaps: List[float] = []
        for a, b in zip(timestamps, timestamps[1:]):
            gap = (b - a).total_seconds()
            if gap > 0:
                gaps.append(gap)
                all_gaps.append(gap)
        if gaps:
            mean_gap = sum(gaps) / len(gaps)
        else:
            mean_gap = 0.0
        per_router[router] = {
            "incident_count": len(timestamps),
            "gap_count": len(gaps),
            "mean_gap_seconds": round(mean_gap, 2),
            "mean_gap_hours": round(mean_gap / 3600.0, 3),
        }

    overall = 0.0
    if all_gaps:
        overall = sum(all_gaps) / len(all_gaps)

    return {
        "overall_mean_seconds": round(overall, 2),
        "overall_mean_hours": round(overall / 3600.0, 3),
        "total_gap_samples": len(all_gaps),
        "per_router": per_router,
    }


def calculate_availability(window_hours: int, skip_influx: bool) -> Dict[str, Any]:
    if skip_influx:
        return {
            "queried": False,
            "window_hours": window_hours,
            "per_router": {},
            "overall": {"samples": 0, "up_samples": 0, "availability_pct": 0.0},
            "note": "Influx query skipped (--no-influx)",
        }
    return _query_ping_availability(window_hours)


# ── ORCHESTRATION ───────────────────────────────────────────────────────────
def build_kpi_report(
    availability_window_hours: int = DEFAULT_AVAILABILITY_WINDOW_HOURS,
    skip_influx: bool = False,
) -> Dict[str, Any]:
    mttd = calculate_mttd()
    mttr = calculate_mttr()
    mtbf = calculate_mtbf()
    availability = calculate_availability(
        window_hours=availability_window_hours,
        skip_influx=skip_influx,
    )

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sources": {
            "incidents_db": str(store.DB_PATH),
            "fault_state": str(FAULT_STATE_PATH),
            "influx_url": INFLUX_URL,
        },
        "mttd": mttd,
        "mttr": mttr,
        "mtbf": mtbf,
        "availability": availability,
    }


# ── DISPLAY ─────────────────────────────────────────────────────────────────
def print_summary(report: Dict[str, Any]) -> None:
    hr = "─" * 78
    print(hr)
    print(" NETWROXIA — KPI REPORT")
    print(hr)

    mttd = report["mttd"]
    print(f" MTTD  mean = {mttd['mean_minutes']:.2f} min "
          f"(samples={mttd['sample_count']})")

    mttr = report["mttr"]
    print(f" MTTR  mean = {mttr['mean_minutes']:.2f} min "
          f"(samples={mttr['sample_count']})")
    for sev, sec in (mttr.get("per_severity_seconds") or {}).items():
        print(f"         {sev:10s}  {sec/60.0:.2f} min")

    mtbf = report["mtbf"]
    print(f" MTBF  overall = {mtbf['overall_mean_hours']:.3f} h "
          f"(gaps={mtbf['total_gap_samples']})")
    for router, m in mtbf["per_router"].items():
        print(f"         {router:18s}  "
              f"incidents={m['incident_count']}  "
              f"mean_gap={m['mean_gap_hours']:.3f} h")

    av = report["availability"]
    print(f" Availability window = {av['window_hours']} h  "
          f"({av['overall']['availability_pct']:.3f}% overall, "
          f"samples={av['overall']['samples']})")
    for router, m in (av.get("per_router") or {}).items():
        print(f"         {router:18s}  "
              f"{m['availability_pct']:.3f}%  "
              f"({m['up_samples']}/{m['samples']})")
    print(hr)


# ── CLI ─────────────────────────────────────────────────────────────────────
def cmd_report(args) -> int:
    report = build_kpi_report(
        availability_window_hours=args.window,
        skip_influx=args.no_influx,
    )
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(report, f, indent=2)
    if args.show:
        print_summary(report)
    else:
        try:
            rel = OUTPUT_PATH.relative_to(PROJECT_ROOT)
        except ValueError:
            rel = OUTPUT_PATH
        print(f"[OK] KPIs written to {rel}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Netwroxia KPI Calculator (D1)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_rep = sub.add_parser("report", help="Compute and write KPI report")
    p_rep.add_argument("--show", action="store_true",
                       help="Pretty-print the report after writing")
    p_rep.add_argument("--window", type=int,
                       default=DEFAULT_AVAILABILITY_WINDOW_HOURS,
                       help="Availability window in hours (default 24)")
    p_rep.add_argument("--no-influx", action="store_true",
                       help="Skip InfluxDB availability query")

    args = parser.parse_args()
    if args.command == "report":
        sys.exit(cmd_report(args))


if __name__ == "__main__":
    main()
