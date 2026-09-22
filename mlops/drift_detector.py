#!/usr/bin/env python3
"""
Netwroxia — Phase D5: Model Drift Detector

Compares live feature vectors against the training distribution using
per-feature z-scores. Flags features (and routers) whose live data has
drifted far from what the model was trained on.

Data sources:
  ml/data/processed/X_*.npy              most recent training matrix
  ml/data/processed/features_*.json      feature names in training order
  ml/inference/predict.py                build_latest_features()

Fallback if predict.py import fails:
  ml/inference/latest_prediction.json    raw_metrics per router

Output: mlops/latest_drift.json

Drift rule:
  z = (live - train_mean) / train_std
  feature drifted if |z| > threshold (default 3.0)
  router status:
    drift_score < 1.5    -> STABLE
    1.5 <= score < 2.5   -> WATCH
    score >= 2.5         -> DRIFTED

CLI:
    python3 mlops/drift_detector.py report
    python3 mlops/drift_detector.py report --show
    python3 mlops/drift_detector.py report --threshold 2.5
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# ── PATH SETUP ──────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ── CONFIG ──────────────────────────────────────────────────────────────────
PROCESSED_DIR = PROJECT_ROOT / "ml" / "data" / "processed"
PREDICTION_PATH = PROJECT_ROOT / "ml" / "inference" / "latest_prediction.json"
OUTPUT_PATH = PROJECT_ROOT / "mlops" / "latest_drift.json"

DEFAULT_THRESHOLD = 3.0
ZERO_STD_EPSILON = 1e-9

# Status tiers by mean(|z|)
STABLE_MAX = 1.5
WATCH_MAX = 2.5


# ── HELPERS ─────────────────────────────────────────────────────────────────
def _safe_relpath(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _load_latest_training() -> Tuple[Optional[np.ndarray], Optional[List[str]], Optional[str]]:
    """
    Load most recent (X, feature_names, source_filename).
    Returns (None, None, None) if nothing usable found.
    """
    x_files = sorted(PROCESSED_DIR.glob("X_*.npy"))
    feat_files = sorted(PROCESSED_DIR.glob("features_*.json"))

    if not x_files:
        return None, None, None

    latest_x = x_files[-1]
    try:
        X = np.load(latest_x, allow_pickle=True).astype(np.float32)
    except Exception:
        return None, None, None

    features: List[str] = []
    if feat_files:
        try:
            with open(feat_files[-1]) as f:
                features = json.load(f)
        except (json.JSONDecodeError, OSError):
            features = []

    if not features or len(features) != X.shape[1]:
        # Fallback: generate placeholder names
        features = [f"feature_{i}" for i in range(X.shape[1])]

    return X, features, latest_x.name


def _build_live_features() -> Optional[List[Dict[str, Any]]]:
    """
    Try predict.py's feature builder first. Returns list of per-router
    dicts with the training feature names, or None on failure.
    """
    try:
        from ml.inference.predict import (
            build_latest_features,
            FEATURE_NAMES,
            ROUTERS,
        )
    except ImportError:
        return None

    try:
        features_df, _raw_df = build_latest_features()
    except Exception:
        return None

    if features_df is None or features_df.empty:
        return None

    out: List[Dict[str, Any]] = []
    for i, router in enumerate(ROUTERS):
        if i >= len(features_df):
            break
        row = features_df.iloc[i]
        out.append({
            "router": router,
            "features": {name: float(row[name]) for name in FEATURE_NAMES},
        })
    return out


def _build_live_from_prediction() -> Optional[List[Dict[str, Any]]]:
    """
    Fallback: read latest_prediction.json raw_metrics. Uses the same
    FEATURE_NAMES order as predict.py, filling unknown features with 0.
    """
    if not PREDICTION_PATH.exists():
        return None
    try:
        with open(PREDICTION_PATH) as f:
            pred = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    # Try to fetch FEATURE_NAMES via import; else hardcode the known list
    try:
        from ml.inference.predict import FEATURE_NAMES, ROUTERS
    except ImportError:
        FEATURE_NAMES = [
            "average_response_ms", "percent_packet_loss", "count",
            "state_ok", "cpu_percent", "mem_percent",
            "router_BR-Koramangala", "router_BR-Whitefield",
            "router_HO-Chennai", "router_ZO-Bengaluru",
        ]
        ROUTERS = ["HO-Chennai", "ZO-Bengaluru",
                   "BR-Koramangala", "BR-Whitefield"]

    router_one_hot = {f"router_{r}": r for r in ROUTERS}

    predictions = pred.get("predictions", [])
    out: List[Dict[str, Any]] = []
    for p in predictions:
        router = p.get("router")
        if router not in ROUTERS:
            continue
        raw = p.get("raw_metrics", {}) or {}
        features: Dict[str, float] = {}
        features["average_response_ms"] = float(raw.get("latency_ms") or 0.0)
        features["percent_packet_loss"] = float(raw.get("packet_loss_pct") or 0.0)
        features["count"] = float(raw.get("ospf_neighbors") or 0.0)
        features["state_ok"] = 1.0 if raw.get("bgp_established") else 0.0
        features["cpu_percent"] = float(raw.get("cpu_pct") or 0.0)
        features["mem_percent"] = float(raw.get("mem_pct") or 0.0)
        for feat_name, r in router_one_hot.items():
            features[feat_name] = 1.0 if r == router else 0.0
        out.append({"router": router, "features": features})
    return out or None


# ── DRIFT COMPUTATION ───────────────────────────────────────────────────────
def compute_feature_stats(
    X: np.ndarray,
    features: List[str],
) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    for i, name in enumerate(features):
        col = X[:, i]
        out[name] = {
            "mean": float(np.mean(col)),
            "std": float(np.std(col)),
        }
    return out


def compute_drift_for_router(
    live: Dict[str, float],
    stats: Dict[str, Dict[str, float]],
    threshold: float,
) -> Dict[str, Any]:
    feature_results: List[Dict[str, Any]] = []
    abs_z: List[float] = []

    for fname, fstat in stats.items():
        std = fstat["std"]
        mean = fstat["mean"]
        live_val = live.get(fname)

        if live_val is None:
            feature_results.append({
                "feature": fname,
                "train_mean": round(mean, 6),
                "train_std": round(std, 6),
                "live_value": None,
                "z_score": None,
                "drifted": False,
                "reason": "missing_live_value",
            })
            continue

        if std < ZERO_STD_EPSILON:
            feature_results.append({
                "feature": fname,
                "train_mean": round(mean, 6),
                "train_std": round(std, 6),
                "live_value": round(float(live_val), 6),
                "z_score": None,
                "drifted": False,
                "reason": "zero_variance_skipped",
            })
            continue

        z = (live_val - mean) / std
        z_f = float(z)
        drifted = abs(z_f) > threshold
        abs_z.append(abs(z_f))

        feature_results.append({
            "feature": fname,
            "train_mean": round(mean, 6),
            "train_std": round(std, 6),
            "live_value": round(float(live_val), 6),
            "z_score": round(z_f, 4),
            "drifted": drifted,
            "reason": "drifted" if drifted else "within_threshold",
        })

    drift_score = round(float(np.mean(abs_z)), 4) if abs_z else 0.0
    if drift_score < STABLE_MAX:
        status = "STABLE"
    elif drift_score < WATCH_MAX:
        status = "WATCH"
    else:
        status = "DRIFTED"

    drifted_features = [r["feature"] for r in feature_results if r["drifted"]]

    return {
        "drift_score": drift_score,
        "status": status,
        "drifted_features": drifted_features,
        "drifted_feature_count": len(drifted_features),
        "features": feature_results,
    }


# ── ORCHESTRATION ───────────────────────────────────────────────────────────
def build_drift_report(threshold: float = DEFAULT_THRESHOLD) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)

    X, features, source_file = _load_latest_training()

    if X is None or not features:
        return {
            "timestamp": now.isoformat(),
            "status": "no_training_data",
            "reason": "no usable X_*.npy in ml/data/processed/",
            "threshold": threshold,
            "sources": {
                "processed_dir": _safe_relpath(PROCESSED_DIR),
                "prediction": _safe_relpath(PREDICTION_PATH),
            },
            "routers": [],
        }

    # Prefer real live features; fallback to prediction file
    live = _build_live_features()
    live_source = "predict.py"
    if not live:
        live = _build_live_from_prediction()
        live_source = "latest_prediction.json"

    stats = compute_feature_stats(X, features)

    routers_out: List[Dict[str, Any]] = []
    overall_drifted_features: set = set()

    if live:
        for entry in live:
            res = compute_drift_for_router(entry["features"], stats, threshold)
            res["router"] = entry["router"]
            routers_out.append(res)
            overall_drifted_features.update(res["drifted_features"])

    overall_score = (
        round(float(np.mean([r["drift_score"] for r in routers_out])), 4)
        if routers_out else 0.0
    )
    if overall_score < STABLE_MAX:
        overall_status = "STABLE"
    elif overall_score < WATCH_MAX:
        overall_status = "WATCH"
    else:
        overall_status = "DRIFTED"

    return {
        "timestamp": now.isoformat(),
        "status": "ok",
        "threshold": threshold,
        "training_source": source_file,
        "live_source": live_source if live else "none",
        "training_samples": int(X.shape[0]),
        "feature_count": len(features),
        "sources": {
            "processed_dir": _safe_relpath(PROCESSED_DIR),
            "prediction": _safe_relpath(PREDICTION_PATH),
        },
        "overall": {
            "drift_score": overall_score,
            "status": overall_status,
            "routers_evaluated": len(routers_out),
            "drifted_features_union": sorted(overall_drifted_features),
        },
        "routers": routers_out,
    }


# ── DISPLAY ─────────────────────────────────────────────────────────────────
def print_summary(report: Dict[str, Any]) -> None:
    hr = "─" * 78
    print(hr)
    print(" NETWROXIA — DRIFT DETECTOR")
    print(hr)

    if report["status"] != "ok":
        print(f" Status : {report['status']}")
        print(f" Reason : {report.get('reason', '')}")
        print(hr)
        return

    ov = report["overall"]
    print(f" Threshold        : {report['threshold']:.2f}σ")
    print(f" Training source  : {report['training_source']}")
    print(f" Live source      : {report['live_source']}")
    print(f" Training samples : {report['training_samples']}")
    print(f" Features         : {report['feature_count']}")
    print(f" Overall score    : {ov['drift_score']:.4f}  [{ov['status']}]")
    print(hr)

    for r in report["routers"]:
        print(f" {r['router']:18s}  "
              f"score={r['drift_score']:>6.4f}  "
              f"[{r['status']:8s}]  "
              f"drifted={r['drifted_feature_count']}")
        for f in r["features"]:
            if f["drifted"]:
                print(f"     DRIFT {f['feature']:24s}  "
                      f"z={f['z_score']:+.3f}  "
                      f"train μ±σ={f['train_mean']:.4f}±{f['train_std']:.4f}  "
                      f"live={f['live_value']}")
        if r["drifted_features"]:
            print(f"     flagged: {', '.join(r['drifted_features'])}")
    print(hr)


# ── CLI ─────────────────────────────────────────────────────────────────────
def cmd_report(args) -> int:
    report = build_drift_report(threshold=args.threshold)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(report, f, indent=2, default=str)
    rel = _safe_relpath(OUTPUT_PATH)
    if args.show:
        print_summary(report)
    else:
        if report["status"] == "ok":
            ov = report["overall"]
            print(f"[OK] drift report written to {rel}  "
                  f"score={ov['drift_score']:.4f}  [{ov['status']}]")
        else:
            print(f"[OK] drift report written to {rel}  "
                  f"status={report['status']}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Netwroxia Drift Detector (D5)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_rep = sub.add_parser("report")
    p_rep.add_argument("--show", action="store_true")
    p_rep.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)

    args = parser.parse_args()
    if args.command == "report":
        sys.exit(cmd_report(args))


if __name__ == "__main__":
    main()
