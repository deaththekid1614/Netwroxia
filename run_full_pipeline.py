#!/usr/bin/env python3
"""
Netwroxia — Phase D10: Full Pipeline Runner

Chains Stage 1-4 (existing run_pipeline.py) with Phase A/B/C/D into one
command. Phase selection, dry-run, per-step timeouts, never fails
catastrophically — always produces a summary.

Stages:
  1-4   existing run_pipeline.py (Containerlab + Telegraf + ML + Copilot)
  A     Beacons + fault library readiness
  B     Analytics + Impact + RCA + Explainability
  C     Incident engine + Execution cycle
  D     KPI + SLA + Trends + MLOps + Reports

CLI:
    python3 run_full_pipeline.py run
    python3 run_full_pipeline.py run --dry-run
    python3 run_full_pipeline.py run --phases A,B,C,D
    python3 run_full_pipeline.py run --phases B,C
    python3 run_full_pipeline.py run --skip-preflight
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── CONFIG ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent

# ANSI colors (matching existing run_pipeline.py)
R = "\033[91m"
G = "\033[92m"
Y = "\033[93m"
B = "\033[94m"
C = "\033[96m"
W = "\033[97m"
D = "\033[0m"
BD = "\033[1m"


# ── STEP DEFINITIONS ────────────────────────────────────────────────────────
# Each step: (step_id, title, command_as_list, timeout_seconds)
# Commands are run via subprocess.run with list args (no shell).
PHASES: Dict[str, List[Tuple[str, str, List[str], int]]] = {
    "1-4": [
        ("1.1", "Containerlab health check",
         ["python3", "network/verify/health_check.py"], 30),
        ("1.2", "Legacy pipeline stages 1-4",
         ["python3", "run_pipeline.py"], 900),
    ],
    "A": [
        ("A.1", "Beacon sender (one cycle)",
         ["python3", "beacons/beacon_sender.py", "--once"], 30),
        ("A.2", "Beacon collector",
         ["python3", "beacons/beacon_collector.py"], 30),
    ],
    "B": [
        ("B.1", "Health score",
         ["python3", "analytics/health_score.py"], 30),
        ("B.2", "Impact analyzer",
         ["python3", "impact/impact_analyzer.py"], 30),
        ("B.3", "Business impact",
         ["python3", "impact/business_impact.py"], 30),
        ("B.4", "RCA correlation",
         ["python3", "rca/correlation_engine.py"], 30),
        ("B.5", "RCA causal graph",
         ["python3", "rca/causal_graph.py"], 30),
        ("B.6", "RCA historical match",
         ["python3", "rca/historical_matcher.py"], 30),
        ("B.7", "RCA output",
         ["python3", "rca/rca_output.py"], 30),
        ("B.8", "SHAP explainer",
         ["python3", "explain/shap_explainer.py"], 60),
        ("B.9", "Explanation output",
         ["python3", "explain/explanation_output.py"], 30),
    ],
    "C": [
        ("C.1", "Incident engine tick",
         ["python3", "incidents/incident_engine.py", "tick"], 60),
        ("C.2", "Orchestrator cycle",
         ["python3", "remediation/engine/orchestrator.py", "cycle"], 120),
    ],
    "D": [
        ("D.1", "KPI calculator",
         ["python3", "analytics/kpi_calculator.py", "report"], 30),
        ("D.2", "SLA tracker",
         ["python3", "analytics/sla_tracker.py", "report"], 30),
        ("D.3", "Trend engine",
         ["python3", "analytics/trend_engine.py", "report"], 30),
        ("D.4", "Model registry",
         ["python3", "mlops/model_registry.py", "report"], 30),
        ("D.5", "Drift detector",
         ["python3", "mlops/drift_detector.py", "report"], 60),
        ("D.6", "Performance tracker",
         ["python3", "mlops/performance_tracker.py", "report"], 30),
        ("D.7", "Daily report",
         ["python3", "reports/daily_report.py", "generate"], 30),
        ("D.8", "Weekly report",
         ["python3", "reports/weekly_report.py", "generate"], 30),
    ],
}

VALID_PHASE_KEYS = list(PHASES.keys())
DEFAULT_PHASES = VALID_PHASE_KEYS


# ── PRINTING HELPERS ────────────────────────────────────────────────────────
def banner(text: str, char: str = "═") -> None:
    w = 70
    print(f"\n{B}{BD}{char*w}{D}")
    print(f"{B}{BD}  {text:<{w-4}}{D}")
    print(f"{B}{BD}{char*w}{D}")


def ok(m: str) -> None:
    print(f"{G}  ✓ {m}{D}")


def warn(m: str) -> None:
    print(f"{Y}  ! {m}{D}")


def fail(m: str) -> None:
    print(f"{R}  ✗ {m}{D}")


def info(m: str) -> None:
    print(f"{C}  → {m}{D}")


def note(m: str) -> None:
    print(f"{W}  {m}{D}")


def sep() -> None:
    print(f"{W}  {'─'*66}{D}")


def section(title: str) -> None:
    print(f"\n{W}{BD}  ▶ {title}{D}")


# ── EXECUTION ───────────────────────────────────────────────────────────────
def run_step(
    step_id: str,
    title: str,
    cmd: List[str],
    timeout: int,
    dry_run: bool,
) -> Dict[str, Any]:
    """
    Execute one step via subprocess. Returns:
      {step_id, title, cmd, ok, duration_s, stdout_tail, stderr_tail, error}
    """
    print(f"\n  [{step_id}] {title}")
    note(f"        $ {' '.join(cmd)}")

    if dry_run:
        info("        [DRY-RUN] skipped")
        return {
            "step_id": step_id, "title": title, "cmd": cmd,
            "ok": True, "duration_s": 0.0, "dry_run": True,
            "stdout_tail": "", "stderr_tail": "", "error": None,
        }

    started = time.time()
    try:
        proc = subprocess.run(
            cmd, cwd=str(PROJECT_ROOT),
            capture_output=True, text=True, timeout=timeout,
        )
        duration = time.time() - started
        success = proc.returncode == 0
        stdout_tail = "\n".join((proc.stdout or "").strip().split("\n")[-3:])
        stderr_tail = "\n".join((proc.stderr or "").strip().split("\n")[-3:])

        if success:
            ok(f"        done in {duration:.1f}s")
        else:
            fail(f"        exit={proc.returncode} after {duration:.1f}s")
            if stderr_tail:
                for line in stderr_tail.split("\n"):
                    print(f"{R}        {line}{D}")

        return {
            "step_id": step_id, "title": title, "cmd": cmd,
            "ok": success, "duration_s": round(duration, 2), "dry_run": False,
            "stdout_tail": stdout_tail, "stderr_tail": stderr_tail,
            "error": None,
        }
    except subprocess.TimeoutExpired:
        duration = time.time() - started
        fail(f"        TIMEOUT after {timeout}s")
        return {
            "step_id": step_id, "title": title, "cmd": cmd,
            "ok": False, "duration_s": round(duration, 2), "dry_run": False,
            "stdout_tail": "", "stderr_tail": "", "error": "timeout",
        }
    except FileNotFoundError as e:
        fail(f"        command not found: {e}")
        return {
            "step_id": step_id, "title": title, "cmd": cmd,
            "ok": False, "duration_s": 0.0, "dry_run": False,
            "stdout_tail": "", "stderr_tail": str(e), "error": "not_found",
        }


def run_preflight(dry_run: bool, skip: bool) -> bool:
    """
    Run beacons/system_check.py --wait --timeout 60. Returns True if
    ready (or skipped/dry-run).
    """
    section("PRE-FLIGHT: System readiness check")
    if skip:
        warn("pre-flight skipped (--skip-preflight)")
        return True

    if dry_run:
        info("[DRY-RUN] would run: system_check.py --wait --timeout 60")
        return True

    cmd = ["python3", "beacons/system_check.py", "--wait", "--timeout", "60"]
    try:
        proc = subprocess.run(
            cmd, cwd=str(PROJECT_ROOT),
            capture_output=True, text=True, timeout=90,
        )
        if proc.returncode == 0:
            ok("system ready")
            return True
        warn(f"system_check exit={proc.returncode}")
        tail = "\n".join((proc.stdout or "").strip().split("\n")[-5:])
        for line in tail.split("\n"):
            print(f"{Y}        {line}{D}")
        return False
    except subprocess.TimeoutExpired:
        warn("pre-flight timed out after 90s")
        return False
    except FileNotFoundError:
        warn("beacons/system_check.py not found")
        return False


# ── ORCHESTRATION ───────────────────────────────────────────────────────────
def run_pipeline(
    phases: List[str],
    dry_run: bool = False,
    skip_preflight: bool = False,
) -> Dict[str, Any]:
    started = time.time()
    started_at = datetime.now(timezone.utc)

    print(f"\n{B}{BD}{'▓'*70}{D}")
    print(f"{B}{BD}  NETWROXIA — FULL PIPELINE RUNNER (D10){D}")
    print(f"{B}{BD}  IBM Z Datathon 2026 | Team Astro_X{D}")
    print(f"{B}{BD}  Phases: {', '.join(phases) if phases else '(none)'}{D}")
    print(f"{B}{BD}  Mode: {'DRY-RUN' if dry_run else 'LIVE'}{D}")
    print(f"{B}{BD}  {started_at.strftime('%Y-%m-%d %H:%M:%S')} UTC{D}")
    print(f"{B}{BD}{'▓'*70}{D}")

    preflight_ok = run_preflight(dry_run=dry_run, skip=skip_preflight)

    results: List[Dict[str, Any]] = []
    for phase_key in phases:
        steps = PHASES.get(phase_key, [])
        banner(f"PHASE {phase_key} ({len(steps)} steps)")
        for step_id, title, cmd, timeout in steps:
            results.append(run_step(step_id, title, cmd, timeout, dry_run))

    elapsed = time.time() - started
    total = len(results)
    passed = sum(1 for r in results if r["ok"])
    failed = total - passed

    print(f"\n{B}{BD}{'▓'*70}{D}")
    print(f"{B}{BD}  RUN SUMMARY{D}")
    print(f"{B}{BD}{'▓'*70}{D}")
    print(f"  Pre-flight      : {'OK' if preflight_ok else 'WARN'}")
    print(f"  Phases run      : {', '.join(phases)}")
    print(f"  Steps total     : {total}")
    print(f"  Steps passed    : {G}{passed}{D}")
    print(f"  Steps failed    : {R if failed else G}{failed}{D}")
    print(f"  Elapsed         : {elapsed:.1f}s")
    print()

    if failed > 0:
        section("FAILED STEPS")
        for r in results:
            if not r["ok"]:
                fail(f"  [{r['step_id']}] {r['title']}  "
                     f"(error={r.get('error') or 'exit'})")
        print()

    section("KEY OUTPUTS (last updated)")
    outputs = [
        ("Prediction", "ml/inference/latest_prediction.json"),
        ("Copilot", "copilot/llm/latest_copilot_response.json"),
        ("Beacon state", "beacons/latest_beacon_state.json"),
        ("Beacon health", "beacons/latest_beacon_health.json"),
        ("Health score", "analytics/latest_health.json"),
        ("Impact", "impact/latest_impact.json"),
        ("Business impact", "impact/latest_business_impact.json"),
        ("RCA", "rca/latest_rca.json"),
        ("Explanation", "explain/latest_explanation.json"),
        ("KPIs", "analytics/latest_kpis.json"),
        ("SLA", "analytics/latest_sla.json"),
        ("Trends", "analytics/latest_trends.json"),
        ("Registry", "mlops/latest_registry.json"),
        ("Drift", "mlops/latest_drift.json"),
        ("Performance", "mlops/latest_performance.json"),
        ("Daily report", "reports/daily/latest.json"),
        ("Weekly report", "reports/weekly/latest.json"),
    ]
    for label, rel in outputs:
        path = PROJECT_ROOT / rel
        if path.exists():
            ts = datetime.fromtimestamp(
                path.stat().st_mtime, tz=timezone.utc
            ).strftime("%H:%M:%S")
            size = path.stat().st_size
            ok(f"  {label:16s} {rel:44s}  {size:>7d}B  {ts}")
        else:
            warn(f"  {label:16s} {rel:44s}  (missing)")

    print()
    print(f"{B}{BD}{'▓'*70}{D}\n")

    return {
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "phases": phases,
        "dry_run": dry_run,
        "preflight_ok": preflight_ok,
        "total": total,
        "passed": passed,
        "failed": failed,
        "elapsed_s": round(elapsed, 2),
        "results": results,
    }


# ── CLI ─────────────────────────────────────────────────────────────────────
def _parse_phases(spec: Optional[str]) -> List[str]:
    if not spec:
        return list(DEFAULT_PHASES)
    wanted = [p.strip() for p in spec.split(",") if p.strip()]
    bad = [p for p in wanted if p not in PHASES]
    if bad:
        raise ValueError(f"unknown phase(s): {', '.join(bad)}  "
                         f"(valid: {', '.join(VALID_PHASE_KEYS)})")
    # Preserve canonical order
    return [p for p in VALID_PHASE_KEYS if p in wanted]


def cmd_run(args) -> int:
    try:
        phases = _parse_phases(args.phases)
    except ValueError as e:
        print(f"{R}[FATAL] {e}{D}")
        return 2

    summary = run_pipeline(
        phases=phases,
        dry_run=args.dry_run,
        skip_preflight=args.skip_preflight,
    )
    # Exit code: 0 if all passed, 1 if any failed
    return 0 if summary["failed"] == 0 else 1


def cmd_list(_args) -> int:
    print("Available phases and their steps:")
    for key, steps in PHASES.items():
        print(f"\n  PHASE {key} ({len(steps)} steps)")
        for sid, title, cmd, timeout in steps:
            print(f"    {sid:5s}  {title:40s}  timeout={timeout}s")
            print(f"           $ {' '.join(cmd)}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Netwroxia Full Pipeline Runner (D10)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Run the full pipeline")
    p_run.add_argument("--dry-run", action="store_true",
                       help="Enumerate steps without executing")
    p_run.add_argument("--phases", default=None,
                       help=f"Comma-separated subset of "
                            f"{','.join(VALID_PHASE_KEYS)} (default: all)")
    p_run.add_argument("--skip-preflight", action="store_true",
                       help="Skip the system_check readiness gate")

    sub.add_parser("list", help="List all phases and steps")

    args = parser.parse_args()
    if args.command == "run":
        sys.exit(cmd_run(args))
    if args.command == "list":
        sys.exit(cmd_list(args))


if __name__ == "__main__":
    main()
