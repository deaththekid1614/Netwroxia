#!/usr/bin/env python3
"""
Netwroxia — Phase D4: Model Registry

Scans ml/models/ for every trained model and metrics file, produces a
registry view with per-model type, age, size, metrics, and the identity
of the model predict.py will load right now.

Output: mlops/latest_registry.json

Sources (read-only):
  ml/models/*.pkl              trained classifiers
  ml/models/*.pt               trained LSTM
  ml/models/metrics_*.json     training metrics per model
  ml/inference/predict.py      discovery order (mirrored)

CLI:
    python3 mlops/model_registry.py report [--show]
    python3 mlops/model_registry.py list
    python3 mlops/model_registry.py cleanup --dry-run
    python3 mlops/model_registry.py cleanup --yes
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── PATH SETUP ──────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ── CONFIG ──────────────────────────────────────────────────────────────────
MODELS_DIR = PROJECT_ROOT / "ml" / "models"
OUTPUT_PATH = PROJECT_ROOT / "mlops" / "latest_registry.json"

# Same priority order as ml/inference/predict.py:load_latest_xgboost()
XGB_SLOT_PATTERNS = [
    "xgboost_reg_*.pkl",
    "xgboost_*.pkl",
    "randomforest_*.pkl",
    "isolation_forest_*.pkl",
]
LSTM_SLOT_PATTERNS = ["lstm_predictor_*.pt"]

# Timestamp pattern in filenames: YYYYMMDD_HHMMSS
TS_RE = re.compile(r"(\d{8}_\d{6})")


# ── HELPERS ─────────────────────────────────────────────────────────────────
def _safe_relpath(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _classify(filename: str) -> str:
    """Map filename to a model type."""
    stem = filename
    if stem.startswith("xgboost_reg_"):
        return "xgboost_classifier"
    if stem.startswith("xgboost_"):
        return "xgboost_classifier"
    if stem.startswith("randomforest_"):
        return "randomforest_classifier"
    if stem.startswith("isolation_forest_"):
        return "isolation_forest"
    if stem.startswith("lstm_predictor_"):
        return "lstm_predictor"
    if stem.startswith("lstm_"):
        return "lstm"
    return "unknown"


def _extract_timestamp(filename: str) -> Optional[str]:
    m = TS_RE.search(filename)
    return m.group(1) if m else None


def _metrics_path_for(model_path: Path) -> Optional[Path]:
    """
    Given a model file, find its metrics_*.json sibling.
    Matching rule: metrics file contains the same YYYYMMDD_HHMMSS token.
    """
    ts = _extract_timestamp(model_path.stem)
    if ts is None:
        return None
    candidates = sorted(MODELS_DIR.glob(f"metrics_*{ts}*.json"))
    return candidates[-1] if candidates else None


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


# ── SCANNING ────────────────────────────────────────────────────────────────
def _model_type_of_file(path: Path) -> Optional[str]:
    """Return model type for a file we care about, or None to skip."""
    if path.suffix == ".pkl":
        return _classify(path.stem)
    if path.suffix == ".pt":
        return _classify(path.stem)
    return None


def scan_models() -> List[Dict[str, Any]]:
    if not MODELS_DIR.exists():
        return []

    now = datetime.now(timezone.utc)
    records: List[Dict[str, Any]] = []

    for path in sorted(MODELS_DIR.iterdir()):
        mtype = _model_type_of_file(path)
        if mtype is None:
            continue

        try:
            stat = path.stat()
        except OSError:
            continue

        modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        age_hours = (now - modified).total_seconds() / 3600.0

        metrics_path = _metrics_path_for(path)
        metrics_data = _load_json(metrics_path) if metrics_path else None

        records.append({
            "filename": path.name,
            "type": mtype,
            "size_bytes": stat.st_size,
            "modified_at": modified.isoformat(),
            "age_hours": round(age_hours, 3),
            "metrics_file": metrics_path.name if metrics_path else None,
            "metrics": metrics_data,
        })

    # Mark latest-of-type
    latest_by_type: Dict[str, str] = {}
    for r in sorted(records, key=lambda x: x["modified_at"]):
        latest_by_type[r["type"]] = r["filename"]
    for r in records:
        r["is_latest_of_type"] = (latest_by_type.get(r["type"]) == r["filename"])

    # Sort newest first within each type
    records.sort(key=lambda x: (x["type"], x["modified_at"]), reverse=True)
    return records


def find_currently_used() -> Dict[str, Optional[str]]:
    """
    Mimic predict.py's discovery to show which model files would be
    loaded right now.
    """
    out = {"xgboost_slot": None, "lstm_slot": None}

    for pattern in XGB_SLOT_PATTERNS:
        files = sorted(MODELS_DIR.glob(pattern)) if MODELS_DIR.exists() else []
        if files:
            out["xgboost_slot"] = files[-1].name
            break

    for pattern in LSTM_SLOT_PATTERNS:
        files = sorted(MODELS_DIR.glob(pattern)) if MODELS_DIR.exists() else []
        if files:
            out["lstm_slot"] = files[-1].name
            break

    return out


# ── ORCHESTRATION ───────────────────────────────────────────────────────────
def build_registry() -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    models = scan_models()

    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for m in models:
        by_type.setdefault(m["type"], []).append(m)

    type_summary = {
        mtype: {
            "count": len(items),
            "latest_file": next(
                (i["filename"] for i in items if i["is_latest_of_type"]),
                None,
            ),
            "latest_modified_at": items[0]["modified_at"] if items else None,
        }
        for mtype, items in by_type.items()
    }

    return {
        "timestamp": now.isoformat(),
        "models_dir": _safe_relpath(MODELS_DIR),
        "total_models": len(models),
        "by_type": type_summary,
        "currently_used": find_currently_used(),
        "models": models,
    }


# ── DISPLAY ─────────────────────────────────────────────────────────────────
def print_summary(reg: Dict[str, Any]) -> None:
    hr = "─" * 78
    print(hr)
    print(" NETWROXIA — MODEL REGISTRY")
    print(hr)
    print(f" Total models tracked : {reg['total_models']}")

    cu = reg["currently_used"]
    print(f" Currently loaded XGB slot : {cu['xgboost_slot'] or '-'}")
    print(f" Currently loaded LSTM slot: {cu['lstm_slot'] or '-'}")
    print(hr)

    for mtype, s in sorted(reg["by_type"].items()):
        print(f" TYPE {mtype}  (count={s['count']})")
        print(f"   latest: {s['latest_file']}")
        print(f"   modified: {s['latest_modified_at']}")
        for m in reg["models"]:
            if m["type"] != mtype:
                continue
            latest_tag = " [LATEST]" if m["is_latest_of_type"] else ""
            metrics_tag = ""
            if m.get("metrics"):
                keys = []
                for k in ("precision", "recall", "f1"):
                    v = m["metrics"].get(k)
                    if isinstance(v, (int, float)):
                        keys.append(f"{k}={v:.3f}")
                if keys:
                    metrics_tag = "  " + " ".join(keys)
            print(f"   {m['filename']:50s} "
                  f"{m['size_bytes']:>8d}B  "
                  f"age={m['age_hours']:>7.2f}h"
                  f"{latest_tag}{metrics_tag}")
        print()


def cmd_list(_args) -> int:
    reg = build_registry()
    cu = reg["currently_used"]
    print(f"XGB slot : {cu['xgboost_slot']}")
    print(f"LSTM slot: {cu['lstm_slot']}")
    print(f"Total    : {reg['total_models']}")
    for m in reg["models"]:
        print(f"  {m['type']:22s}  {m['filename']}")
    return 0


# ── CLEANUP ─────────────────────────────────────────────────────────────────
def cmd_cleanup(args) -> int:
    reg = build_registry()
    stale: List[Dict[str, Any]] = [
        m for m in reg["models"] if not m["is_latest_of_type"]
    ]
    if not stale:
        print("[OK] no stale models found")
        return 0

    print(f"[{'DRY-RUN' if args.dry_run else 'LIVE'}] "
          f"{len(stale)} stale model(s):")
    for m in stale:
        print(f"  {m['filename']:50s} age={m['age_hours']:>7.2f}h  "
              f"type={m['type']}")

    if args.dry_run:
        print("[DRY-RUN] no files deleted")
        return 0
    if not args.yes:
        print("[ABORT] pass --yes to delete (or --dry-run to preview)")
        return 1

    for m in stale:
        path = MODELS_DIR / m["filename"]
        try:
            path.unlink()
            print(f"  [DELETED] {m['filename']}")
        except OSError as e:
            print(f"  [ERROR]   {m['filename']}: {e}")

    # Delete matched metrics if they belong to a stale model
    for m in stale:
        if m.get("metrics_file"):
            mpath = MODELS_DIR / m["metrics_file"]
            if mpath.exists():
                try:
                    mpath.unlink()
                    print(f"  [DELETED] {m['metrics_file']}")
                except OSError as e:
                    print(f"  [ERROR]   {m['metrics_file']}: {e}")

    print(f"[OK] cleanup complete")
    return 0


# ── CLI ─────────────────────────────────────────────────────────────────────
def cmd_report(args) -> int:
    reg = build_registry()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(reg, f, indent=2, default=str)
    if args.show:
        print_summary(reg)
    else:
        print(f"[OK] registry written to {_safe_relpath(OUTPUT_PATH)}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Netwroxia Model Registry (D4)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_rep = sub.add_parser("report", help="Scan + write registry")
    p_rep.add_argument("--show", action="store_true")

    sub.add_parser("list", help="Flat list of current state")

    p_cl = sub.add_parser("cleanup", help="Delete all but latest of each type")
    p_cl.add_argument("--dry-run", action="store_true")
    p_cl.add_argument("--yes", action="store_true")

    args = parser.parse_args()
    if args.command == "report":
        sys.exit(cmd_report(args))
    if args.command == "list":
        sys.exit(cmd_list(args))
    if args.command == "cleanup":
        sys.exit(cmd_cleanup(args))


if __name__ == "__main__":
    main()
