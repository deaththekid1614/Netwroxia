#!/usr/bin/env python3
"""
Netwroxia — Phase D3: Trend Engine

Roll up incidents into time buckets so trends are visible:
  hourly (24) / daily (14) / weekly (8) / monthly (6)

Per-bucket metrics:
  incidents_total
  incidents_by_severity   {CRITICAL, HIGH, MEDIUM, LOW}
  downtime_weighted_seconds
  unique_routers
  mttr_seconds            (mean over resolved incidents in bucket)

Output: analytics/latest_trends.json

Design notes:
  - Buckets are anchored to NOW (not wall-clock boundaries). The most
    recent bucket ends at the current moment.
  - Empty buckets are included so the dashboard renders flat lines.
  - Severity weights are imported from sla_tracker (D2) for consistency.

CLI:
    python3 analytics/trend_engine.py report
    python3 analytics/trend_engine.py report --bucket hourly --count 24
    python3 analytics/trend_engine.py report --bucket weekly --count 8
    python3 analytics/trend_engine.py report --show
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── PATH SETUP ──────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from incidents import incident_store as store
from analytics.sla_tracker import SEVERITY_WEIGHT

# ── CONFIG ──────────────────────────────────────────────────────────────────
OUTPUT_PATH = PROJECT_ROOT / "analytics" / "latest_trends.json"

VALID_BUCKETS = ("hourly", "daily", "weekly", "monthly")

DEFAULT_BUCKET = "daily"
DEFAULT_COUNT = 14

BUCKET_SECONDS = {
    "hourly": 3600,
    "daily": 86400,
    "weekly": 7 * 86400,
    "monthly": 30 * 86400,
}


# ── HELPERS ─────────────────────────────────────────────────────────────────
def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _safe_relpath(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _bucket_start(now: datetime, bucket: str, idx: int, count: int) -> datetime:
    """
    Compute the start of the idx-th bucket, where idx=0 is the oldest and
    idx=count-1 is the newest. The newest bucket ends at `now`.
    """
    step = BUCKET_SECONDS[bucket]
    # Bucket (count-1-i) ends at now - i*step
    end_offset = (count - 1 - idx) * step
    return now - timedelta(seconds=end_offset + step)


def _bucket_end(now: datetime, bucket: str, idx: int, count: int) -> datetime:
    step = BUCKET_SECONDS[bucket]
    end_offset = (count - 1 - idx) * step
    return now - timedelta(seconds=end_offset)


def _overlap_seconds(
    start_a: datetime, end_a: datetime,
    start_b: datetime, end_b: datetime,
) -> float:
    """Duration of intersection between two time intervals, in seconds."""
    s = max(start_a, start_b)
    e = min(end_a, end_b)
    return max(0.0, (e - s).total_seconds())


# ── BUCKETING ───────────────────────────────────────────────────────────────
def compute_bucket_metrics(
    incidents: List[Dict[str, Any]],
    b_start: datetime,
    b_end: datetime,
) -> Dict[str, Any]:
    """
    Compute the metrics for one time bucket given the full incident list.
    Only incidents whose [created_at, resolved_at] overlaps the bucket
    contribute.
    """
    counts_by_sev = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    weighted_downtime = 0.0
    routers: set = set()
    mttr_samples: List[float] = []

    for inc in incidents:
        created = _parse_iso(inc.get("created_at"))
        if created is None:
            continue
        resolved = _parse_iso(inc.get("resolved_at"))
        end = resolved if resolved is not None else b_end

        overlap = _overlap_seconds(created, end, b_start, b_end)
        if overlap <= 0:
            continue

        sev = (inc.get("severity") or "LOW").upper()
        if sev not in counts_by_sev:
            sev = "LOW"

        # Downtime always accumulates on overlap (spans buckets)
        weight = SEVERITY_WEIGHT.get(sev, 0.1)
        weighted_downtime += overlap * weight

        # Counts / routers / MTTR only for incidents STARTED in this bucket.
        # This prevents double-counting when an incident sits exactly on a
        # bucket boundary.
        if not (b_start <= created < b_end):
            continue

        counts_by_sev[sev] += 1

        router = inc.get("root_router") or ""
        if router:
            routers.add(router)

        # MTTR only contributes if the incident actually resolved
        if resolved is not None:
            duration = (resolved - created).total_seconds()
            if duration >= 0:
                mttr_samples.append(duration)

    mean_mttr = (sum(mttr_samples) / len(mttr_samples)) if mttr_samples else 0.0

    return {
        "incidents_total": sum(counts_by_sev.values()),
        "incidents_by_severity": dict(counts_by_sev),
        "downtime_weighted_seconds": round(weighted_downtime, 2),
        "unique_routers": sorted(routers),
        "unique_router_count": len(routers),
        "mttr_seconds": round(mean_mttr, 2),
        "mttr_sample_count": len(mttr_samples),
    }


def build_buckets(
    incidents: List[Dict[str, Any]],
    bucket: str,
    count: int,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    if bucket not in VALID_BUCKETS:
        raise ValueError(f"unknown bucket: {bucket}")
    if count < 1:
        raise ValueError("count must be >= 1")

    if now is None:
        now = datetime.now(timezone.utc)

    buckets: List[Dict[str, Any]] = []
    for idx in range(count):
        b_start = _bucket_start(now, bucket, idx, count)
        b_end = _bucket_end(now, bucket, idx, count)
        metrics = compute_bucket_metrics(incidents, b_start, b_end)
        metrics["bucket_index"] = idx
        metrics["start"] = b_start.isoformat()
        metrics["end"] = b_end.isoformat()
        metrics["duration_seconds"] = int((b_end - b_start).total_seconds())
        buckets.append(metrics)

    return buckets


# ── AGGREGATION ACROSS BUCKETS ──────────────────────────────────────────────
def summarize_trends(buckets: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Derive overall trend signals from a bucket list:
      - first_half vs second_half incident counts (directional trend)
      - peak bucket
      - trend_label
    """
    if not buckets:
        return {
            "trend_label": "no_data",
            "first_half_incidents": 0,
            "second_half_incidents": 0,
            "delta_incidents": 0,
            "peak_bucket_index": None,
            "peak_incidents": 0,
        }

    n = len(buckets)
    mid = n // 2
    first_half = buckets[:mid] if mid > 0 else []
    second_half = buckets[mid:]

    fh_total = sum(b["incidents_total"] for b in first_half)
    sh_total = sum(b["incidents_total"] for b in second_half)
    delta = sh_total - fh_total

    # Trend label thresholds: relative change
    if fh_total == 0 and sh_total == 0:
        label = "flat"
    elif fh_total == 0:
        label = "rising"
    else:
        change_pct = (delta / fh_total) * 100.0
        if change_pct > 20:
            label = "rising"
        elif change_pct < -20:
            label = "falling"
        else:
            label = "stable"

    peak_idx = max(range(n), key=lambda i: buckets[i]["incidents_total"])
    peak_inc = buckets[peak_idx]["incidents_total"]

    return {
        "trend_label": label,
        "first_half_incidents": fh_total,
        "second_half_incidents": sh_total,
        "delta_incidents": delta,
        "peak_bucket_index": peak_idx,
        "peak_incidents": peak_inc,
    }


# ── ORCHESTRATION ───────────────────────────────────────────────────────────
def build_trend_report(
    bucket: str = DEFAULT_BUCKET,
    count: int = DEFAULT_COUNT,
) -> Dict[str, Any]:
    if bucket not in VALID_BUCKETS:
        raise ValueError(f"unknown bucket: {bucket}")

    now = datetime.now(timezone.utc)
    incidents = store.list_incidents(limit=5000)
    buckets = build_buckets(incidents, bucket, count, now=now)
    summary = summarize_trends(buckets)

    return {
        "timestamp": now.isoformat(),
        "bucket": bucket,
        "bucket_count": count,
        "bucket_seconds": BUCKET_SECONDS[bucket],
        "window_start": buckets[0]["start"] if buckets else now.isoformat(),
        "window_end": buckets[-1]["end"] if buckets else now.isoformat(),
        "sources": {
            "incidents_db": str(store.DB_PATH),
        },
        "summary": summary,
        "buckets": buckets,
    }


# ── DISPLAY ─────────────────────────────────────────────────────────────────
def print_summary(report: Dict[str, Any]) -> None:
    hr = "─" * 78
    print(hr)
    print(f" NETWROXIA — TREND REPORT "
          f"({report['bucket']}, {report['bucket_count']} buckets)")
    print(hr)

    s = report["summary"]
    print(f" Trend label      : {s['trend_label']}")
    print(f" First half       : {s['first_half_incidents']} incidents")
    print(f" Second half      : {s['second_half_incidents']} incidents")
    print(f" Delta            : {s['delta_incidents']:+d}")
    print(f" Peak             : bucket #{s['peak_bucket_index']} "
          f"({s['peak_incidents']} incidents)")
    print(hr)

    for b in report["buckets"]:
        sev = b["incidents_by_severity"]
        bar_len = b["incidents_total"]
        bar = "█" * min(bar_len, 40)
        print(f"  #{b['bucket_index']:02d}  "
              f"{b['start'][:16]} → {b['end'][:16]}  "
              f"n={b['incidents_total']:3d}  "
              f"C/H/M/L={sev['CRITICAL']}/{sev['HIGH']}/"
              f"{sev['MEDIUM']}/{sev['LOW']}  "
              f"down={b['downtime_weighted_seconds']:.0f}s  "
              f"routers={b['unique_router_count']}  "
              f"{bar}")

    print(hr)


# ── CLI ─────────────────────────────────────────────────────────────────────
def cmd_report(args) -> int:
    report = build_trend_report(bucket=args.bucket, count=args.count)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(report, f, indent=2)
    rel = _safe_relpath(OUTPUT_PATH)
    if args.show:
        print_summary(report)
    else:
        print(f"[OK] Trend report written to {rel}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Netwroxia Trend Engine (D3)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_rep = sub.add_parser("report", help="Compute and write trend report")
    p_rep.add_argument("--bucket", choices=VALID_BUCKETS, default=DEFAULT_BUCKET)
    p_rep.add_argument("--count", type=int, default=DEFAULT_COUNT)
    p_rep.add_argument("--show", action="store_true")

    args = parser.parse_args()
    if args.command == "report":
        sys.exit(cmd_report(args))


if __name__ == "__main__":
    main()
