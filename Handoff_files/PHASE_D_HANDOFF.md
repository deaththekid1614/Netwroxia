═══════════════════════════════════════════════════════════════════════════════
                     NETWROXIA — PHASE D HANDOFF DOCUMENT
                    Analytics & Reporting Layer: KPIs, SLA, Trends, MLOps
                    IBM Z Datathon 2026 | Team Astro_X | Wildcard Entry
═══════════════════════════════════════════════════════════════════════════════

Generated: 2026-09-22
Project root: /home/death-kid/IDE/netwroxia/
Machine: MacBook Air 2015, Zorin OS 16.3 x86_64, 8GB RAM, i5-5250U
Status: COMPLETE — 10 files verified, all tests green, zero new dependencies

───────────────────────────────────────────────────────────────────────────────
1. WHAT PHASE D IS
───────────────────────────────────────────────────────────────────────────────

Phase D turns raw artifacts from Phases A/B/C into MEASURABLE, REPORTABLE
signals — the layer that answers "how are we doing?" rather than
"what is happening right now?"

Phase D answers:
  - What are the NOC KPIs?                      (D1)
  - Are we meeting banking SLAs?                (D2)
  - How are trends moving?                      (D3)
  - What models are deployed?                   (D4)
  - Has the input distribution drifted?         (D5)
  - Is prediction accuracy holding up?          (D6)
  - What happened today?                        (D7)
  - What happened this week?                    (D8)
  - What happened in one incident?              (D9)
  - How do I run everything?                    (D10)

Phase D is read-only against Phase A/B/C artifacts. It adds no new
network behavior, no new containers, no new dependencies.

───────────────────────────────────────────────────────────────────────────────
2. FILES CREATED IN PHASE D
───────────────────────────────────────────────────────────────────────────────

D1  analytics/kpi_calculator.py
    MTTD, MTTR, MTBF, Availability. MTTD pairs incidents with
    preceding fault injections (from fault_sim state). Availability
    from InfluxDB ping measurement. Per-router MTBF breakdown.
    Output: analytics/latest_kpis.json
    CLI: report [--show] [--window N] [--no-influx]

D2  analytics/sla_tracker.py
    Per-service and per-branch SLA compliance against targets in
    impact/service_map.json (B2). Severity-weighted downtime
    (CRITICAL=1.0, HIGH=0.7, MEDIUM=0.4, LOW=0.1). Current
    impairment flags from B1 health. Windowed (default 30 days).
    Output: analytics/latest_sla.json
    CLI: report [--show] [--window N] [--no-current]

D3  analytics/trend_engine.py
    Time-bucketed rollups: hourly (24), daily (14), weekly (8),
    monthly (6). Counts anchor to start-bucket (no double-counting
    on boundaries). Downtime spans buckets. Trend label from
    first-half vs second-half incident counts.
    Output: analytics/latest_trends.json
    CLI: report [--show] [--bucket X] [--count N]

D4  mlops/model_registry.py
    Scans ml/models/*.pkl + *.pt. Classifies by filename prefix.
    Matches metrics_*.json siblings by timestamp. Marks latest-of-
    type. Mirrors predict.py discovery order for "currently used".
    Optional cleanup command (delete all but latest per type).
    Output: mlops/latest_registry.json
    CLI: report [--show], list, cleanup [--dry-run|--yes]

D5  mlops/drift_detector.py
    Compares live feature vectors against training distribution
    (ml/data/processed/X_*.npy). Per-feature z-score. Flags |z|>
    threshold (default 3.0σ). Status tiers: STABLE (<1.5), WATCH
    (1.5-2.5), DRIFTED (>=2.5). Reuses predict.py's feature builder.
    Output: mlops/latest_drift.json
    CLI: report [--show] [--threshold X]

D6  mlops/performance_tracker.py
    Precision/recall/F1 from predictions paired with incidents.
    Match rule: says_fault = combined_alert != NORMAL or
    xgboost.predicted_fault. Tolerance window (default ±30 min).
    Append-only history log for trend over time.
    Output: mlops/latest_performance.json + prediction_history.jsonl
    CLI: report [--show] [--tolerance N], record, history [--show]

D7  reports/daily_report.py
    Aggregates D1-D6 + B1 into single daily snapshot. Every section
    degrades gracefully. Writes to reports/daily/daily_YYYY-MM-DD.json
    plus reports/daily/latest.json mirror.
    CLI: generate [--show] [--date YYYY-MM-DD]

D8  reports/weekly_report.py
    Aggregates 7 daily reports into ISO-week rollup. Monday-Sunday
    alignment. Comparison vs previous week when available.
    Output: reports/weekly/weekly_YYYY-WNN.json + latest.json
    CLI: generate [--show] [--end YYYY-MM-DD]

D9  reports/incident_report.py
    Per-incident deep dive. Merges C2 transitions + C1 audit trail
    + C7 snapshots + C13 escalations into a single chronological
    timeline. Includes RCA snapshot, actions taken, counts.
    Output: reports/incidents/<incident_id>.json
    CLI: list, generate <id> [--show] [--verbose], latest [--show]

D10 run_full_pipeline.py
    Unified pipeline runner. Chains Stage 1-4 (via existing
    run_pipeline.py) with Phase A/B/C/D. Phase selection via
    --phases. Pre-flight readiness gate. Dry-run mode. Never fails
    catastrophically — always produces a summary.
    CLI: run [--dry-run] [--phases X,Y] [--skip-preflight], list

───────────────────────────────────────────────────────────────────────────────
3. DIRECTORIES ADDED OR MODIFIED
───────────────────────────────────────────────────────────────────────────────

analytics/                    (extended)
  kpi_calculator.py           D1
  sla_tracker.py              D2
  trend_engine.py             D3
  latest_kpis.json            generated
  latest_sla.json             generated
  latest_trends.json          generated

mlops/                        (new in Phase D)
  model_registry.py           D4
  drift_detector.py           D5
  performance_tracker.py      D6
  latest_registry.json        generated
  latest_drift.json           generated
  latest_performance.json     generated
  prediction_history.jsonl    append-only

reports/                      (new in Phase D)
  daily_report.py             D7
  weekly_report.py            D8
  incident_report.py          D9
  daily/
    daily_YYYY-MM-DD.json     per day
    latest.json               mirror
  weekly/
    weekly_YYYY-WNN.json      per ISO week
    latest.json               mirror
  incidents/
    <incident_id>.json        per incident

run_full_pipeline.py          D10 (top-level)

───────────────────────────────────────────────────────────────────────────────
4. DATA FLOW
───────────────────────────────────────────────────────────────────────────────

Phase A + B + C artifacts
    │
    ▼
┌──────────────────────────────────────────────────────────────────┐
│ D10 run_full_pipeline.py                                          │
│  ├─ Pre-flight: beacons/system_check.py --wait                    │
│  ├─ Optional: run_pipeline.py (Stage 1-4)                         │
│  ├─ Phase A: beacons (sender + collector)                         │
│  ├─ Phase B: health → impact → business → RCA → SHAP              │
│  ├─ Phase C: incident tick → orchestrator cycle                   │
│  └─ Phase D: kpis → sla → trends → registry → drift →             │
│              performance → daily → weekly                          │
└─────────────────────────────┬────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────────┐
        │ D7 daily_report.py                          │
        │   reads: kpis + sla + trends + health       │
        │          + registry + drift + performance   │
        │   writes: reports/daily/daily_<date>.json   │
        │           reports/daily/latest.json         │
        └─────────────────────┬───────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────────┐
        │ D8 weekly_report.py                         │
        │   reads: 7× reports/daily/daily_*.json      │
        │   writes: reports/weekly/weekly_<WNN>.json  │
        │           reports/weekly/latest.json        │
        └─────────────────────────────────────────────┘

        ┌─────────────────────────────────────────────┐
        │ D9 incident_report.py                       │
        │   reads: incidents.db + audit_logs +        │
        │          snapshots + escalations            │
        │   writes: reports/incidents/<id>.json       │
        └─────────────────────────────────────────────┘

───────────────────────────────────────────────────────────────────────────────
5. CLI REFERENCE
───────────────────────────────────────────────────────────────────────────────

── D1 KPIs ────────────────────────────────────────────────────────────

python3 analytics/kpi_calculator.py report
python3 analytics/kpi_calculator.py report --show
python3 analytics/kpi_calculator.py report --window 48
python3 analytics/kpi_calculator.py report --no-influx

── D2 SLA ─────────────────────────────────────────────────────────────

python3 analytics/sla_tracker.py report
python3 analytics/sla_tracker.py report --show
python3 analytics/sla_tracker.py report --window 7
python3 analytics/sla_tracker.py report --no-current

── D3 Trends ──────────────────────────────────────────────────────────

python3 analytics/trend_engine.py report
python3 analytics/trend_engine.py report --show
python3 analytics/trend_engine.py report --bucket hourly --count 24
python3 analytics/trend_engine.py report --bucket weekly --count 8

── D4 Model Registry ─────────────────────────────────────────────────

python3 mlops/model_registry.py report --show
python3 mlops/model_registry.py list
python3 mlops/model_registry.py cleanup --dry-run

── D5 Drift ───────────────────────────────────────────────────────────

python3 mlops/drift_detector.py report --show
python3 mlops/drift_detector.py report --threshold 2.5

── D6 Performance ─────────────────────────────────────────────────────

python3 mlops/performance_tracker.py report --show
python3 mlops/performance_tracker.py record
python3 mlops/performance_tracker.py history --show

── D7 Daily ───────────────────────────────────────────────────────────

python3 reports/daily_report.py generate --show
python3 reports/daily_report.py generate --date 2026-09-20

── D8 Weekly ──────────────────────────────────────────────────────────

python3 reports/weekly_report.py generate --show
python3 reports/weekly_report.py generate --end 2026-09-20

── D9 Incident ────────────────────────────────────────────────────────

python3 reports/incident_report.py list
python3 reports/incident_report.py latest --show
python3 reports/incident_report.py generate <incident_id> --show --verbose

── D10 Full Pipeline ─────────────────────────────────────────────────

python3 run_full_pipeline.py list
python3 run_full_pipeline.py run --dry-run
python3 run_full_pipeline.py run --phases D --skip-preflight
python3 run_full_pipeline.py run --phases A,B,C,D
python3 run_full_pipeline.py run                # all phases (slow)

───────────────────────────────────────────────────────────────────────────────
6. KPI DEFINITIONS
───────────────────────────────────────────────────────────────────────────────

MTTD (Mean Time To Detect)
  For each incident, find the most recent fault injected (from
  fault_sim/active_faults.json) within 60 min before incident.created.
  MTTD_sample = incident.created - fault.applied_at. Mean over all
  samples. Empty data => 0.

MTTR (Mean Time To Resolve)
  (resolved_at - created_at) for incidents where both exist.
  Mean + per-severity breakdown. Unresolved incidents excluded.

MTBF (Mean Time Between Failures)
  Per router: mean gap between consecutive incident created_at times.
  Overall: mean across all gaps. Single incident => 0 gaps.

Availability
  From InfluxDB ping measurement. Per-router: samples with
  percent_packet_loss < 5% / total samples. Window default 24h.

───────────────────────────────────────────────────────────────────────────────
7. SLA SEVERITY WEIGHTS
───────────────────────────────────────────────────────────────────────────────

Not every incident is a full outage. Downtime is weighted by
severity to reflect realistic impact:

  CRITICAL  1.0  total outage
  HIGH      0.7  significant degradation
  MEDIUM    0.4  partial degradation
  LOW       0.1  negligible

service downtime = sum over affecting incidents of
                   (incident_duration_seconds * severity_weight)

actual_uptime_pct = (window_seconds - downtime) / window_seconds * 100
compliant = actual_uptime_pct >= sla_target_pct  (from service_map)

───────────────────────────────────────────────────────────────────────────────
8. TEST STATUS — ALL GREEN
───────────────────────────────────────────────────────────────────────────────

D1  KPIs: MTTD/MTTR/MTBF/availability, window, no-influx     40/40 ✓
D2  SLA: severity weights, propagation, breach, impairment   42/42 ✓
D3  Trends: buckets, boundary anchoring, severity weights    44/44 ✓
D4  Registry: classification, metrics match, cleanup         44/44 ✓
D5  Drift: z-scores, zero-variance, missing values, tiers    35/35 ✓
D6  Performance: TP/FP/FN/TN, tolerance, history             34/34 ✓
D7  Daily: sections, missing sources, CLI writes             45/45 ✓
D8  Weekly: ISO weeks, aggregate, compare, partial           48/48 ✓
D9  Incident: transitions, audit, snapshots, sanitize        50/50 ✓
D10 Pipeline: phases, steps, dry-run, failure count          110/110 ✓

Live validation:
  D2  live: 10/10 services compliant, no impairment
  D5  live: all 4 routers STABLE, score 0.65
  D6  live: TP=0 FP=1 FN=0 TN=3 (BR-Whitefield FP from old pred)
  D7  live: full daily report, all 7 sources available
  D8  live: 1/7 days included (only today has data)
  D9  live: incident 216bf1a5369b with 20-entry timeline
  D10 live: Phase D run 8/8 steps, 3.8s total

No side effects confirmed after every phase:
  beacons/system_check.py            SYSTEM READY
  network/verify/health_check.py     ALL CHECKS PASSED

───────────────────────────────────────────────────────────────────────────────
9. KNOWN LIMITATIONS
───────────────────────────────────────────────────────────────────────────────

1. D1 MTTD pairs incidents with faults via a 60-min lookback window.
   Real-world MTTD may need a longer window or explicit linkage.

2. D2 severity weights are heuristic. A CRITICAL that lasts 1 second
   weighs the same as a CRITICAL that lasts 1 hour, scaled by duration.
   Fine for demo, needs calibration for production.

3. D3 empty buckets are included so the dashboard can render flat
   lines. If you want sparse output, filter out zero-count buckets
   in the consumer.

4. D4 cleanup command deletes files. Requires --yes to execute. Do
   not run cleanup on production models.

5. D5 drift detection requires regenerated training data. Empty
   ml/data/processed/ returns status="no_training_data" gracefully.

6. D6 precision/recall in demo mode may show FP=1/TN=3 because the
   LSTM's stale prediction on BR-Whitefield doesn't have a matching
   incident. That's honest signal — the LSTM is over-predicting.

7. D7-D9 reports are JSON + terminal text. HTML/PDF rendering is
   left to Phase E (dashboard) or a future enhancement.

8. D10's full run calls run_pipeline.py (Stage 1-4), which triggers
   Mistral 7B inference (~20 min). Use --phases to skip when
   iterating on analytics.

───────────────────────────────────────────────────────────────────────────────
10. WHAT'S NEXT — PHASE E PREVIEW
───────────────────────────────────────────────────────────────────────────────

Phase E (Dashboard 2.0) — 10 files:

  E1  dashboard/pages/overview.py        existing Overview, refreshed
  E2  dashboard/pages/beacon_monitor.py  NEW: live beacon RTT + baseline
  E3  dashboard/pages/health_score.py    NEW: 0-100 network health
  E4  dashboard/pages/incidents.py       NEW: incident lifecycle view
  E5  dashboard/pages/rca.py             NEW: RCA + causal graph
  E6  dashboard/pages/impact.py          NEW: banking service impact
  E7  dashboard/pages/remediation.py     NEW: approvals + action log
  E8  dashboard/pages/mlops.py           NEW: models + drift + perf
  E9  dashboard/pages/reports.py         NEW: daily/weekly/incident
  E10 dashboard/pages/audit.py           NEW: audit trail viewer
  Plus: dashboard/components/role_switcher.py

Phase E reads all artifacts from A/B/C/D. Uses st.session_state to
share router snapshot. Adds a role selector (NOC / Admin / Exec).

Phase E is 100% read-only against artifacts. No new dependencies.

═══════════════════════════════════════════════════════════════════════════════
END OF PHASE D HANDOFF
Phase D: COMPLETE | Files: 10 | Tests: 492/492 pass | Ready for Phase E
═══════════════════════════════════════════════════════════════════════════════
