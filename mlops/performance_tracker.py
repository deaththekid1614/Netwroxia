#!/usr/bin/env python3
"""
Netwroxia — Phase D6: Performance Tracker

Computes rolling precision/recall/F1 by pairing current predictions with
confirmed incidents. Optionally tracks performance over time via a small
append-only JSONL history file.

Data sources:
  ml/inference/latest_prediction.json    predictions
  incidents/incidents.db                 confirmed incidents
  mlops/prediction_history.jsonl         appended by `record`

Match rule (per router):
  Prediction "says fault" when combined_alert != NORMAL OR
                            xgboost.predicted_fault is True.

  TP  prediction says fault, incident within ±tolerance window
  FP  prediction says fault, no incident in window
  FN  prediction says healthy, incident in window
  TN  prediction says healthy, no incident in window

Output: mlops/latest_performance.json

CLI:
    python3 mlops/performance_tracker.py report [--show] [--tolerance 30]
    python3 mlops/performance_tracker.py record
    python3 mlops/performance_tracker.py history [--show]
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── PATH SETUP ──────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from incidents import incident_store as store

# ── CONFIG ──────────────────────────────────────────────────────────────────
PREDICTION_PATH = PROJECT_ROOT / "ml" / "inference" / "latest_prediction.json"
OUTPUT_PATH = PROJECT_ROOT / "mlops" / "latest_performance.json"
HISTORY_PATH = PROJECT_ROOT / "mlops" / "prediction_history.jsonl"

DEFAULT_TOLERANCE_MIN = 30


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


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _prediction_says_fault(pred: Dict[str, Any]) -> bool:
    """A prediction is an alert if combined_alert != NORMAL or xgboost
    predicted_fault is True."""
    alert = (pred.get("combined_alert") or "NORMAL").upper()
    if alert != "NORMAL":
        return True
    xgb = pred.get("xgboost") or {}
    return bool(xgb.get("predicted_fault", False))


def _metrics_from_confusion(tp: int, fp: int, fn: int, tn: int) -> Dict[str, Any]:
    def safe_div(num: float, den: float) -> float:
        return round(num / den, 4) if den > 0 else 0.0

    precision = safe_div(tp, tp + fp)
    recall = safe_div(tp, tp + fn)
    specificity = safe_div(tn, tn + fp)
    f1 = safe_div(2 * precision * recall, precision + recall) \
        if (precision + recall) > 0 else 0.0

    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "specificity": specificity,
        "support": tp + fp + fn + tn,
    }


# ── SNAPSHOT CLASSIFICATION ─────────────────────────────────────────────────
def _incidents_in_window(
    incidents: List[Dict[str, Any]],
    router: str,
    center: datetime,
    tolerance_min: int,
) -> List[Dict[str, Any]]:
    """
    Return incidents whose root_router == router and whose created_at
    falls within [center - tolerance, center + tolerance].
    """
    delta = timedelta(minutes=tolerance_min)
    lo = center - delta
    hi = center + delta

    out: List[Dict[str, Any]] = []
    for inc in incidents:
        if (inc.get("root_router") or "") != router:
            continue
        created = _parse_iso(inc.get("created_at"))
        if created is None:
            continue
        if lo <= created <= hi:
            out.append(inc)
    return out


def classify_snapshot(
    prediction: Dict[str, Any],
    incidents: List[Dict[str, Any]],
    tolerance_min: int,
) -> Dict[str, Any]:
    """
    Compute per-router confusion matrix + aggregate metrics.

    Note: This method matches incidents to a single prediction timestamp.
    If the prediction file has no timestamp, we fall back to now().
    """
    pred_ts = _parse_iso(prediction.get("timestamp")) \
        or datetime.now(timezone.utc)

    predictions = prediction.get("predictions", [])
    per_router: Dict[str, Dict[str, Any]] = {}
    tp = fp = fn = tn = 0

    for p in predictions:
        router = p.get("router") or "unknown"
        says_fault = _prediction_says_fault(p)
        matched = _incidents_in_window(incidents, router, pred_ts, tolerance_min)
        has_incident = len(matched) > 0

        if says_fault and has_incident:
            cls = "TP"; tp += 1
        elif says_fault and not has_incident:
            cls = "FP"; fp += 1
        elif not says_fault and has_incident:
            cls = "FN"; fn += 1
        else:
            cls = "TN"; tn += 1

        per_router[router] = {
            "says_fault": says_fault,
            "incident_matched": has_incident,
            "matched_incident_ids": [i["incident_id"] for i in matched],
            "classification": cls,
            "combined_alert": p.get("combined_alert"),
            "fault_probability": (p.get("xgboost") or {}).get("fault_probability"),
        }

    metrics = _metrics_from_confusion(tp, fp, fn, tn)
    return {
        "prediction_timestamp": pred_ts.isoformat(),
        "tolerance_minutes": tolerance_min,
        "total_predictions": len(predictions),
        "total_incidents_considered": len(incidents),
        "metrics": metrics,
        "per_router": per_router,
    }


# ── HISTORY ─────────────────────────────────────────────────────────────────
def _read_history() -> List[Dict[str, Any]]:
    if not HISTORY_PATH.exists():
        return []
    out: List[Dict[str, Any]] = []
    try:
        with open(HISTORY_PATH) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out


def _append_history(entry: Dict[str, Any]) -> bool:
    """Append unless an entry with the same `snapshot_ts` already exists."""
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_history()
    for e in existing:
        if e.get("snapshot_ts") == entry.get("snapshot_ts"):
            return False  # already present
    with open(HISTORY_PATH, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")
    return True


def compute_history_summary(history: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not history:
        return {
            "entries": 0,
            "mean_precision": 0.0,
            "mean_recall": 0.0,
            "mean_f1": 0.0,
            "trend_label": "no_data",
        }

    precisions = [e["metrics"]["precision"] for e in history if "metrics" in e]
    recalls = [e["metrics"]["recall"] for e in history if "metrics" in e]
    f1s = [e["metrics"]["f1"] for e in history if "metrics" in e]

    def mean(xs: List[float]) -> float:
        return round(sum(xs) / len(xs), 4) if xs else 0.0

    # Trend from first vs second half F1
    n = len(f1s)
    if n < 2:
        trend = "flat"
    else:
        mid = n // 2
        fh = sum(f1s[:mid]) / max(mid, 1)
        sh = sum(f1s[mid:]) / max(n - mid, 1)
        delta = sh - fh
        if delta > 0.05:
            trend = "rising"
        elif delta < -0.05:
            trend = "falling"
        else:
            trend = "flat"

    return {
        "entries": n,
        "mean_precision": mean(precisions),
        "mean_recall": mean(recalls),
        "mean_f1": mean(f1s),
        "trend_label": trend,
    }


# ── ORCHESTRATION ───────────────────────────────────────────────────────────
def build_performance_report(tolerance_min: int = DEFAULT_TOLERANCE_MIN) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    prediction = _load_json(PREDICTION_PATH)
    if prediction is None:
        return {
            "timestamp": now.isoformat(),
            "status": "no_predictions",
            "reason": f"prediction file not found: {_safe_relpath(PREDICTION_PATH)}",
            "tolerance_minutes": tolerance_min,
            "sources": {
                "prediction": _safe_relpath(PREDICTION_PATH),
                "incidents_db": str(store.DB_PATH),
                "history": _safe_relpath(HISTORY_PATH),
            },
        }

    incidents = store.list_incidents(limit=5000)
    snapshot = classify_snapshot(prediction, incidents, tolerance_min)

    history = _read_history()
    history_summary = compute_history_summary(history)

    return {
        "timestamp": now.isoformat(),
        "status": "ok",
        "tolerance_minutes": tolerance_min,
        "sources": {
            "prediction": _safe_relpath(PREDICTION_PATH),
            "incidents_db": str(store.DB_PATH),
            "history": _safe_relpath(HISTORY_PATH),
        },
        "current_snapshot": snapshot,
        "history_available": len(history) > 0,
        "history_summary": history_summary,
    }


def record_snapshot(tolerance_min: int = DEFAULT_TOLERANCE_MIN) -> Dict[str, Any]:
    """Append the current snapshot to history. Returns summary dict."""
    prediction = _load_json(PREDICTION_PATH)
    if prediction is None:
        return {"ok": False, "reason": "no prediction file"}

    incidents = store.list_incidents(limit=5000)
    snapshot = classify_snapshot(prediction, incidents, tolerance_min)

    entry = {
        "snapshot_ts": snapshot["prediction_timestamp"],
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "metrics": snapshot["metrics"],
    }
    inserted = _append_history(entry)
    return {
        "ok": True,
        "inserted": inserted,
        "already_present": not inserted,
        "entry": entry,
    }


def build_history_report() -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    history = _read_history()
    return {
        "timestamp": now.isoformat(),
        "entries": len(history),
        "summary": compute_history_summary(history),
        "history": history,
    }


# ── DISPLAY ─────────────────────────────────────────────────────────────────
def print_summary(report: Dict[str, Any]) -> None:
    hr = "─" * 78
    print(hr)
    print(" NETWROXIA — PERFORMANCE TRACKER")
    print(hr)

    if report.get("status") != "ok":
        print(f" Status : {report.get('status')}")
        print(f" Reason : {report.get('reason', '')}")
        print(hr)
        return

    snap = report["current_snapshot"]
    m = snap["metrics"]
    print(f" Prediction ts  : {snap['prediction_timestamp']}")
    print(f" Tolerance      : ±{report['tolerance_minutes']} min")
    print(f" Predictions    : {snap['total_predictions']}")
    print(f" Incidents seen : {snap['total_incidents_considered']}")
    print(hr)
    print(f" TP={m['tp']}  FP={m['fp']}  FN={m['fn']}  TN={m['tn']}")
    print(f" Precision      : {m['precision']:.4f}")
    print(f" Recall         : {m['recall']:.4f}")
    print(f" F1             : {m['f1']:.4f}")
    print(f" Specificity    : {m['specificity']:.4f}")
    print(hr)

    print(" PER-ROUTER")
    for router, r in snap["per_router"].items():
        print(f"   {router:18s}  "
              f"says_fault={str(r['says_fault']):5s}  "
              f"match={str(r['incident_matched']):5s}  "
              f"cls={r['classification']}  "
              f"alert={r.get('combined_alert')}")
    print(hr)

    if report.get("history_available"):
        hs = report["history_summary"]
        print(f" HISTORY: {hs['entries']} entries  "
              f"mean F1={hs['mean_f1']:.4f}  "
              f"trend={hs['trend_label']}")
        print(hr)


# ── CLI ─────────────────────────────────────────────────────────────────────
def cmd_report(args) -> int:
    report = build_performance_report(tolerance_min=args.tolerance)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(report, f, indent=2, default=str)
    rel = _safe_relpath(OUTPUT_PATH)
    if args.show:
        print_summary(report)
    else:
        print(f"[OK] performance written to {rel}  "
              f"status={report.get('status')}")
    return 0


def cmd_record(args) -> int:
    result = record_snapshot(tolerance_min=args.tolerance)
    if not result.get("ok"):
        print(f"[ERROR] {result.get('reason')}")
        return 1
    if result["inserted"]:
        print(f"[OK] history appended  ts={result['entry']['snapshot_ts']}")
    else:
        print(f"[SKIP] already in history  ts={result['entry']['snapshot_ts']}")
    return 0


def cmd_history(args) -> int:
    report = build_history_report()
    if args.show:
        s = report["summary"]
        print("─" * 78)
        print(f" ENTRIES : {report['entries']}")
        print(f" Mean precision : {s['mean_precision']:.4f}")
        print(f" Mean recall    : {s['mean_recall']:.4f}")
        print(f" Mean F1        : {s['mean_f1']:.4f}")
        print(f" Trend label    : {s['trend_label']}")
        print("─" * 78)
        for e in report["history"]:
            m = e.get("metrics", {})
            print(f"   {e.get('snapshot_ts')}  "
                  f"P={m.get('precision',0):.3f}  "
                  f"R={m.get('recall',0):.3f}  "
                  f"F1={m.get('f1',0):.3f}")
    else:
        print(json.dumps(report["summary"], indent=2))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Netwroxia Performance Tracker (D6)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_rep = sub.add_parser("report")
    p_rep.add_argument("--show", action="store_true")
    p_rep.add_argument("--tolerance", type=int, default=DEFAULT_TOLERANCE_MIN)

    p_rec = sub.add_parser("record")
    p_rec.add_argument("--tolerance", type=int, default=DEFAULT_TOLERANCE_MIN)

    p_his = sub.add_parser("history")
    p_his.add_argument("--show", action="store_true")

    args = parser.parse_args()
    if args.command == "report":
        sys.exit(cmd_report(args))
    if args.command == "record":
        sys.exit(cmd_record(args))
    if args.command == "history":
        sys.exit(cmd_history(args))


if __name__ == "__main__":
    main()
