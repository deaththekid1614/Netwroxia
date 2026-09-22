#!/usr/bin/env python3
"""
Netwroxia — Phase B9: SHAP Explainer

Computes per-feature SHAP contributions for the current XGBoost model's
predictions. Reuses predict.py's feature builder verbatim so feature
order and encoding are guaranteed to match training.

Inputs:
  - ml/inference/predict.py          (imported: load_latest_xgboost, build_latest_features, FEATURE_NAMES)
  - current live metrics             (fetched via predict.py)
  - ml/inference/latest_prediction.json (optional cross-check)

Output: explain/latest_shap.json

Schema:
  {
    "timestamp": iso,
    "model_file": "...",
    "method": "tree_shap",
    "base_value": 0.34,
    "feature_names": [...],
    "routers": [
      {
        "router": "HO-Chennai",
        "fault_probability": 0.017,
        "base_value": 0.34,
        "shap_sum": -0.323,
        "top_contributions": [
          {"feature": "state_ok", "value": 1.0, "shap": -0.15, "direction": "reduces"},
          ...
        ],
        "all_contributions": [...]
      }
    ]
  }

CLI:
    python3 explain/shap_explainer.py
    python3 explain/shap_explainer.py --show
    python3 explain/shap_explainer.py --top 3
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

# ── PATH SETUP ──────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ── REUSE predict.py's exact feature builder ────────────────────────────────
try:
    from ml.inference.predict import (
        load_latest_xgboost,
        build_latest_features,
        FEATURE_NAMES,
        ROUTERS,
    )
except ImportError as e:
    print(f"[FATAL] Could not import from predict.py: {e}")
    print(f"[HINT]  Ensure ml/inference/predict.py exists and PROJECT_ROOT is correct.")
    sys.exit(1)

# ── SHAP import (soft-fail for fallback) ────────────────────────────────────
try:
    import shap
    HAS_SHAP = True
except ImportError:
    HAS_SHAP = False
    print("[WARN] SHAP not installed. Fallback will use feature_importances_ * z-score.")


# ── CONFIG ──────────────────────────────────────────────────────────────────
OUTPUT_PATH = Path(PROJECT_ROOT) / "explain" / "latest_shap.json"


# ── HELPERS ─────────────────────────────────────────────────────────────────
def save_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)

def sigmoid(x: float) -> float:
    """Numerically stable sigmoid."""
    if x >= 0:
        z = np.exp(-x)
        return 1.0 / (1.0 + z)
    z = np.exp(x)
    return z / (1.0 + z)

def extract_shap_arrays(explainer, shap_values) -> tuple:
    """
    Normalize SHAP outputs across versions.
    Returns (sv_2d, base_value_float).
      sv_2d: shape (n_samples, n_features)
    """
    base = explainer.expected_value

    # Base value can be list, np.ndarray, or scalar
    if isinstance(base, (list, np.ndarray)):
        base = float(np.asarray(base).ravel()[-1])
    else:
        base = float(base)

    # shap_values shapes vary:
    #   list[sv_class0, sv_class1]   -> take [1]
    #   np.ndarray (n, f, 2)         -> take [:, :, 1]
    #   np.ndarray (n, f)            -> use as-is
    if isinstance(shap_values, list):
        sv = np.asarray(shap_values[1] if len(shap_values) > 1 else shap_values[0])
    else:
        sv = np.asarray(shap_values)
        if sv.ndim == 3:
            sv = sv[:, :, 1]
        elif sv.ndim == 2:
            pass
        else:
            raise ValueError(f"Unexpected shap_values ndim={sv.ndim}")

    return sv, base


def build_tree_explainer(model):
    """Try model_output='probability'; fall back to default on failure."""
    try:
        return shap.TreeExplainer(model, model_output="probability")
    except Exception:
        return shap.TreeExplainer(model)


def contributions_from_shap(
    feature_names: List[str],
    feature_values: np.ndarray,
    shap_row: np.ndarray,
) -> List[Dict[str, Any]]:
    """Build list of {feature, value, shap, direction} sorted by |shap| desc."""
    out: List[Dict[str, Any]] = []
    for name, val, sv in zip(feature_names, feature_values, shap_row):
        sv_f = float(sv)
        out.append({
            "feature": name,
            "value": round(float(val), 4),
            "shap": round(sv_f, 5),
            "direction": "increases" if sv_f > 0 else ("reduces" if sv_f < 0 else "neutral"),
        })
    out.sort(key=lambda c: -abs(c["shap"]))
    return out


# ── FALLBACK (no SHAP) ──────────────────────────────────────────────────────
def fallback_explanations(model, X: np.ndarray, feature_names: List[str]) -> List[List[Dict[str, Any]]]:
    """
    Approximation: feature_importances_ * signed z-score of value vs column mean.
    Not as rigorous as SHAP but produces useful per-router narratives.
    """
    importances = getattr(model, "feature_importances_", None)
    if importances is None:
        importances = np.ones(len(feature_names)) / len(feature_names)

    col_mean = X.mean(axis=0)
    col_std = X.std(axis=0)
    col_std = np.where(col_std < 1e-9, 1.0, col_std)

    per_router: List[List[Dict[str, Any]]] = []
    for row in X:
        z = (row - col_mean) / col_std
        approx = importances * z
        contributions = contributions_from_shap(feature_names, row, approx)
        per_router.append(contributions)
    return per_router


# ── ORCHESTRATION ───────────────────────────────────────────────────────────
def compute_shap(top_k: int) -> Dict[str, Any]:
    print("[1/3] Loading XGBoost model...")
    model, model_path = load_latest_xgboost()
    if model is None:
        print("[FATAL] No XGBoost model found.")
        sys.exit(1)

    print("[2/3] Building live feature matrix...")
    features_df, _raw_df = build_latest_features()
    if features_df is None or features_df.empty:
        print("[FATAL] Could not build feature matrix.")
        sys.exit(1)

    X = features_df.values.astype(np.float32)
    router_names = list(ROUTERS)

    if len(router_names) != X.shape[0]:
        print(f"[WARN] ROUTERS count ({len(router_names)}) != X rows ({X.shape[0]})")
        router_names = router_names[: X.shape[0]]

    print(f"[INFO] X shape: {X.shape}, features: {len(FEATURE_NAMES)}")

    # Predictions for cross-check
    if hasattr(model, "predict_proba"):
        proba = model.predict_proba(X)[:, 1].astype(float)
    else:
        proba = np.full(X.shape[0], 0.0)

    method = "tree_shap" if HAS_SHAP else "feature_importance_x_zscore"
    base_value = 0.0
    all_contributions: List[List[Dict[str, Any]]] = []

    if HAS_SHAP:
        print("[3/3] Computing SHAP values...")
        try:
            explainer = build_tree_explainer(model)
            shap_values = explainer.shap_values(X)
            sv, base_value = extract_shap_arrays(explainer, shap_values)
            for i in range(X.shape[0]):
                all_contributions.append(
                    contributions_from_shap(FEATURE_NAMES, X[i], sv[i])
                )
        except Exception as e:
            print(f"[WARN] SHAP failed: {type(e).__name__}: {e}")
            print("[WARN] Falling back to feature_importances_ x z-score.")
            method = "feature_importance_x_zscore"
            all_contributions = fallback_explanations(model, X, FEATURE_NAMES)
    else:
        print("[3/3] SHAP unavailable. Using fallback explainer.")
        all_contributions = fallback_explanations(model, X, FEATURE_NAMES)

    routers_out: List[Dict[str, Any]] = []
    for i, router in enumerate(router_names):
        contributions = all_contributions[i]
        shap_sum = round(sum(c["shap"] for c in contributions), 5)
        top = contributions[:top_k]
        log_odds_sum = round(float(base_value) + shap_sum, 5)
        routers_out.append({
            "router": router,
            "fault_probability": round(float(proba[i]), 4),
            "base_value": round(float(base_value), 4),
            "shap_sum": shap_sum,
            "log_odds_sum": log_odds_sum,
            "probability_from_log_odds": round(sigmoid(log_odds_sum), 4),
            "top_contributions": top,
            "all_contributions": contributions,
        })

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model_file": os.path.basename(model_path) if model_path else "unknown",
        "method": method,
        "base_value": round(float(base_value), 4),
        "feature_names": list(FEATURE_NAMES),
        "routers": routers_out,
    }


# ── DISPLAY ─────────────────────────────────────────────────────────────────
def print_summary(report: Dict[str, Any], top_k: int) -> None:
    print("─" * 78)
    print(f" Method     : {report['method']}")
    print(f" Model      : {report['model_file']}")
    print(f" Base value : {report['base_value']:.4f}")
    print(f" Features   : {len(report['feature_names'])}")
    print("─" * 78)

    for entry in report["routers"]:
        print(f" {entry['router']:18s}  prob={entry['fault_probability']:.4f}  "
              f"log_odds(base+sum)={entry['log_odds_sum']:+.4f}  "
              f"sigmoid={entry['probability_from_log_odds']:.4f}")
        for i, c in enumerate(entry["top_contributions"][:top_k], 1):
            sign = "+" if c["shap"] > 0 else ""
            print(f"   #{i} {c['feature']:26s} value={c['value']:>10}  "
                  f"shap={sign}{c['shap']:.5f}  [{c['direction']}]")
        print()
    print("─" * 78)


# ── MAIN ────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Netwroxia SHAP Explainer (B9)"
    )
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--top", type=int, default=5,
                        help="Top-K contributions to include in summary (default 5)")
    args = parser.parse_args()

    print("=" * 78)
    print(" NETWROXIA SHAP EXPLAINER (B9)")
    print("=" * 78)
    print(f" Output : {OUTPUT_PATH.relative_to(PROJECT_ROOT)}")
    print(f" SHAP   : {'available' if HAS_SHAP else 'unavailable — fallback mode'}")
    print("=" * 78)

    report = compute_shap(args.top)
    save_json(OUTPUT_PATH, report)

    if args.show:
        print_summary(report, args.top)
    else:
        top_prob = max(r["fault_probability"] for r in report["routers"])
        print(f"[INFO] Routers: {len(report['routers'])}  "
              f"method={report['method']}  "
              f"max_prob={top_prob:.4f}")

    print(f"[INFO] Report written -> {OUTPUT_PATH.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
