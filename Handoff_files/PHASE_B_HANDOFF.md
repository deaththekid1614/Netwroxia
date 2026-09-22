═══════════════════════════════════════════════════════════════════════════════
                     NETWROXIA — PHASE B HANDOFF DOCUMENT
                    Intelligence Layer: Health, Impact, RCA, Explainability
                    IBM Z Datathon 2026 | Team Astro_X | Wildcard Entry
═══════════════════════════════════════════════════════════════════════════════

Generated: 2026-09-22
Project root: /home/death-kid/IDE/netwroxia/
Machine: MacBook Air 2015, Zorin OS 16.3 x86_64, 8GB RAM, i5-5250U
Status: COMPLETE — 10 files verified, all tests green, no dependency added

───────────────────────────────────────────────────────────────────────────────
1. WHAT PHASE B IS
───────────────────────────────────────────────────────────────────────────────

Phase A produced raw data (beacons, fault library, system readiness).
Phase B turns that data into DECISIONS, EXPLANATIONS, and IMPACT — the
"intelligence layer" that sits between raw signals and remediation.

Phase B answers:
  - How healthy is the network right now?              (B1)
  - Which banking services are affected and how bad?   (B2 + B3 + B4)
  - What is the root cause of correlated symptoms?     (B5 + B6 + B7 + B8)
  - Why did the model predict this?                    (B9 + B10)

Phase B is 100% read-mostly: it reads Phase A + Stage 3 outputs, writes
JSON artifacts. No new dependencies, no containers, no network changes.

───────────────────────────────────────────────────────────────────────────────
2. FILES CREATED IN PHASE B
───────────────────────────────────────────────────────────────────────────────

B1  analytics/health_score.py
    0-100 network score per router and region. Blends A4 beacon health
    (authoritative) with Stage 3 ML predictions (advisory). Applies
    credible floor: beacon OK caps ML-only damage at 60, beacon DEGRADED
    at 30. Infers HO-Chennai (beacon receiver) status from senders.
    Flags ML_MISMATCH when beacon says OK but ML says >70%.
    CLI: --show

B2  impact/service_map.json
    4 routers, 10 banking services, revenue rates, user counts,
    propagation graph, RBI mandates. Pure config — no code.

B3  impact/impact_analyzer.py
    Maps B1 health -> affected routers (direct + propagated). Computes
    affected services (DOWN/DEGRADED/UP), affected users, revenue-per-min,
    and severity (NONE/LOW/MEDIUM/HIGH/CRITICAL).
    CLI: --show

B4  impact/business_impact.py
    Converts B3 impact -> INR financial damage. Loss rate per min/hour,
    projections at 15/30/60/120 min, category breakdown by criticality,
    RBI compliance risk + penalties, value of 5-min early warning.
    Tier: NEGLIGIBLE / MINOR / MAJOR / SEVERE / CATASTROPHIC.
    CLI: --show

B5  rca/correlation_engine.py  (v2)
    Groups affected routers into incidents by root cause. Does its OWN
    correlation (walks the propagation graph) rather than trusting B3's
    propagation_chains. Each incident = one root + downstream routers.
    CLI: --show

B6  rca/causal_graph.py
    Builds directed acyclic graph per incident. Nodes carry status,
    health_score, role, users. Edges = active/inactive propagation.
    Emits Graphviz DOT string per graph for dashboard rendering.
    Confidence = (0.5 + 0.1*depth) * severity_multiplier, capped 0.99.
    CLI: --show, --dot <graph_id>

B7  rca/historical_matcher.py
    Matches current incidents against a corpus of past incidents using
    weighted similarity (root 0.35, role 0.15, depth 0.10, routers 0.20,
    services 0.15, severity 0.05). Returns top-N matches with resolution.
    Seeds 4 realistic past incidents under copilot/knowledge_base/past_incidents/.
    CLI: --show, --top N

B8  rca/rca_output.py
    Consolidates B1/B3/B4/B5/B6/B7 into per-incident RCA document. Blends
    B6 root confidence with B7 historical match (0.6/0.4). Template-driven
    narrative. Empty-state safe (system_healthy=true when no incidents).
    CLI: --show

B9  explain/shap_explainer.py
    TreeSHAP values for XGBoost model. Reuses predict.py's feature
    builder verbatim to guarantee feature order matches training. Handles
    multiple SHAP API versions. Fallback to feature_importances_ * z-score
    if SHAP unavailable. Correct log-odds -> probability conversion.
    CLI: --show, --top K

B10 explain/explanation_output.py
    Converts SHAP values into plain-English explanations. Cross-checks
    SHAP vs LSTM forecast and vs prediction status. Attaches explanations
    to RCA incidents (root router's SHAP text). Template-driven, no LLM.
    CLI: --show

───────────────────────────────────────────────────────────────────────────────
3. DIRECTORIES ADDED OR MODIFIED
───────────────────────────────────────────────────────────────────────────────

analytics/                              (new)
  health_score.py
  latest_health.json                    (generated)

impact/                                 (new)
  service_map.json                      (config)
  impact_analyzer.py
  business_impact.py
  latest_impact.json                    (generated)
  latest_business_impact.json           (generated)

rca/                                    (new)
  correlation_engine.py
  causal_graph.py
  historical_matcher.py
  rca_output.py
  latest_correlation.json               (generated)
  latest_causal_graph.json              (generated)
  latest_historical_match.json          (generated)
  latest_rca.json                       (generated)

explain/                                (new)
  shap_explainer.py
  explanation_output.py
  latest_shap.json                      (generated)
  latest_explanation.json               (generated)

copilot/knowledge_base/past_incidents/  (populated)
  INC-2025-001.json                     HO-Chennai MPLS label stack
  INC-2025-002.json                     BR-Koramangala leased-line
  INC-2025-003.json                     BR-Whitefield BGP drop
  INC-2025-004.json                     ZO-Bengaluru IPSec rekey

───────────────────────────────────────────────────────────────────────────────
4. DATA FLOW
───────────────────────────────────────────────────────────────────────────────

                Stage 3                       Stage 5 (dash)
                predict.py                    dashboard
                    │                              ▲
                    ▼                              │
    ┌──────────────────────────────────────────┐   │
    │ B1  analytics/health_score.py            │   │
    │     reads: beacons/latest_beacon_health  │   │
    │            ml/inference/latest_predict   │   │
    │            impact/service_map            │   │
    │     writes: analytics/latest_health.json │   │
    └──────────────────┬───────────────────────┘   │
                       │                           │
                       ▼                           │
    ┌──────────────────────────────────────────┐   │
    │ B3  impact/impact_analyzer.py            │   │
    │     reads: analytics/latest_health       │   │
    │            impact/service_map            │   │
    │     writes: impact/latest_impact.json    │   │
    └──────────────────┬───────────────────────┘   │
                       │                           │
                       ▼                           │
    ┌──────────────────────────────────────────┐   │
    │ B4  impact/business_impact.py            │   │
    │     reads: impact/latest_impact          │   │
    │     writes: impact/latest_business_impact│   │
    └──────────────────┬───────────────────────┘   │
                       │                           │
                       ▼                           │
    ┌──────────────────────────────────────────┐   │
    │ B5  rca/correlation_engine.py  (v2)      │   │
    │     reads: impact/latest_impact          │   │
    │     writes: rca/latest_correlation.json  │   │
    └──────────────────┬───────────────────────┘   │
                       │                           │
                       ▼                           │
    ┌──────────────────────────────────────────┐   │
    │ B6  rca/causal_graph.py                  │   │
    │     reads: rca/latest_correlation        │   │
    │            analytics/latest_health       │   │
    │            impact/service_map            │   │
    │     writes: rca/latest_causal_graph.json │   │
    │     (+ DOT string per graph)             │   │
    └──────────────────┬───────────────────────┘   │
                       │                           │
                       ▼                           │
    ┌──────────────────────────────────────────┐   │
    │ B7  rca/historical_matcher.py            │   │
    │     reads: rca/latest_causal_graph       │   │
    │            copilot/knowledge_base/past_* │   │
    │     writes: rca/latest_historical_match  │   │
    └──────────────────┬───────────────────────┘   │
                       │                           │
                       ▼                           │
    ┌──────────────────────────────────────────┐   │
    │ B8  rca/rca_output.py                    │   │
    │     reads: B1 + B3 + B4 + B5 + B6 + B7   │   │
    │     writes: rca/latest_rca.json          │   │
    │     (per-incident RCA + narrative)       │   │
    └──────────────────┬───────────────────────┘   │
                       │                           │
                       ▼                           │
    ┌──────────────────────────────────────────┐   │
    │ B9  explain/shap_explainer.py            │   │
    │     reuses: ml/inference/predict.py      │   │
    │     reads: ml/models/xgboost_reg_*.pkl   │   │
    │     writes: explain/latest_shap.json     │   │
    └──────────────────┬───────────────────────┘   │
                       │                           │
                       ▼                           │
    ┌──────────────────────────────────────────┐   │
    │ B10 explain/explanation_output.py        │   │
    │     reads: explain/latest_shap           │   │
    │            ml/inference/latest_predict   │   │
    │            rca/latest_rca                │   │
    │     writes: explain/latest_explanation   │   │
    └──────────────────┬───────────────────────┘   │
                       │                           │
                       ▼                           │
              Phase C (remediation)                │
              Stage 4 (copilot grounding) ◄────────┘

───────────────────────────────────────────────────────────────────────────────
5. CLI REFERENCE
───────────────────────────────────────────────────────────────────────────────

── Health Score (B1) ─────────────────────────────────────────────────────

python3 analytics/health_score.py                # silent
python3 analytics/health_score.py --show         # pretty print

── Impact (B3, B4) ───────────────────────────────────────────────────────

python3 impact/impact_analyzer.py                # silent
python3 impact/impact_analyzer.py --show         # pretty
python3 impact/business_impact.py                # silent
python3 impact/business_impact.py --show         # pretty

── RCA (B5-B8) ───────────────────────────────────────────────────────────

python3 rca/correlation_engine.py --show
python3 rca/causal_graph.py --show
python3 rca/causal_graph.py --dot <graph_id>     # emit DOT
python3 rca/historical_matcher.py --show
python3 rca/historical_matcher.py --top 5
python3 rca/rca_output.py --show

── Explainability (B9, B10) ──────────────────────────────────────────────

python3 explain/shap_explainer.py --show
python3 explain/shap_explainer.py --top 3
python3 explain/explanation_output.py --show

── Full Phase B pipeline (recommended order) ────────────────────────────

cd ~/IDE/netwroxia
python3 beacons/beacon_sender.py --once
python3 beacons/beacon_collector.py
python3 ml/inference/predict.py
python3 analytics/health_score.py
python3 impact/impact_analyzer.py
python3 impact/business_impact.py
python3 rca/correlation_engine.py
python3 rca/causal_graph.py
python3 rca/historical_matcher.py
python3 rca/rca_output.py
python3 explain/shap_explainer.py
python3 explain/explanation_output.py

───────────────────────────────────────────────────────────────────────────────
6. OUTPUT ARTIFACTS (what each file looks like)
───────────────────────────────────────────────────────────────────────────────

analytics/latest_health.json
  network.{score, status, router_count, region_count}
  regions.<name>.{score, status, router_count, user_count, routers[]}
  routers.<name>.{
    score, status, region, role, user_count,
    components.{
      beacon_status, beacon_score, beacon_penalty,
      beacon_loss_pct, beacon_inferred,
      xgboost_fault_prob, xgboost_penalty,
      lstm_future_prob, lstm_penalty, ml_total_penalty,
      credible_floor, credible_floor_applied,
      bgp_established, safety_cap_applied, ml_mismatch
    }
  }

impact/latest_impact.json
  overall_impact.{
    severity, affected_router_count, affected_service_count,
    services_down, services_degraded,
    rbi_mandated_services_down, affected_user_count,
    affected_branch_count, total_revenue_per_min_inr
  }
  affected_routers.<name>.{
    directly_affected, via_propagation_from, propagation_chain,
    score, status, role, location, user_count, revenue_per_min_inr
  }
  affected_services[]  { service_id, name, criticality, rbi_mandated,
                          availability, sla_uptime_pct,
                          revenue_per_min_inr,
                          affected_hosts[], healthy_hosts[],
                          host_count_total }
  propagation_chains[]

impact/latest_business_impact.json
  current_loss.{per_minute_inr, per_hour_inr, tier, ...}
  projections.{min_15, min_30, min_60, min_120}
  category_breakdown[]
  rbi_compliance.{applies, breach_thresholds_min, projected_breaches[],
                   estimated_penalty_inr, narrative}
  value_of_early_warning.{lead_time_min, prevented_loss_inr, narrative}

rca/latest_correlation.json
  summary.{incident_count, severity_counts, total_affected_routers,
           total_users_affected, total_revenue_per_min_inr,
           has_critical_incident, top_incident_id, top_root}
  incidents[] {
    incident_id, root, root_role, root_location,
    affected_routers[], downstream_routers[],
    affected_routers_count, downstream_count,
    total_users_affected, total_revenue_per_min_inr,
    statuses[], worst_status, severity, severity_score,
    directly_affected_flags{}
  }

rca/latest_causal_graph.json
  summary.{graph_count, highest_confidence}
  graphs[] {
    graph_id, incident_id, title,
    root_cause.{router, role, severity, confidence,
                propagation_depth, affected_count},
    nodes[].{id, label, role, status, health_score,
             affected, user_count, revenue_per_min_inr},
    edges[].{source, target, direction, status},
    dot
  }

rca/latest_historical_match.json
  corpus_size
  summary.{current_incident_count, incidents_with_matches,
           highest_match_score}
  matches[] {
    current_incident_id, current_root, current_severity,
    current_propagation_depth, current_affected_routers[],
    matches[] {
      match_score, component_scores{},
      past_incident_id, past_title, past_severity,
      past_root_cause, past_resolution,
      past_recovery_time_min, past_rbi_reportable,
      past_occurred_at, past_source_file
    },
    match_count, top_match_score
  }

rca/latest_rca.json
  system_healthy, network_status, network_score
  summary.{incident_count, critical_count, high_count,
           top_incident_id, top_root}
  overall_narrative
  incidents[] {
    incident_id,
    summary.{root, root_role, severity, confidence,
             propagation_depth, chain[], affected_count},
    root_cause.{router, role, location, graph_confidence,
                historical_match_score, combined_confidence},
    propagation.{chain, depth, downstream_count, graph_id},
    impact.{severity, affected_routers, affected_services,
            affected_users, revenue_per_min_inr},
    business.{loss_per_min_inr, loss_per_min_display,
              rbi_penalty_inr, rbi_penalty_display},
    historical.{top_match_id, top_match_score, past_severity,
                past_root_cause, past_resolution,
                past_recovery_time_min, past_rbi_reportable,
                runner_up_id},
    narrative
  }

explain/latest_shap.json
  model_file, method ("tree_shap" or "feature_importance_x_zscore"),
  base_value, feature_names[]
  routers[] {
    router, fault_probability, base_value, shap_sum,
    log_odds_sum, probability_from_log_odds,
    top_contributions[], all_contributions[]
  }

explain/latest_explanation.json
  shap_method
  summary.{router_count, incident_count,
           highest_fault_probability, has_conflicting_signals}
  overall_narrative
  router_explanations.<name>.{
    router, fault_probability, explanation_text,
    top_contributors[],
    lstm_cross_check.{compared, shap_probability,
                      lstm_future_probability, gap, agreement},
    prediction_cross_check.{compared, shap_probability,
                            prediction_status, alignment}
  }
  incident_explanations[].{
    incident_id, root, severity, confidence,
    explanation, top_contributors[]
  }

───────────────────────────────────────────────────────────────────────────────
7. KEY DESIGN DECISIONS
───────────────────────────────────────────────────────────────────────────────

1. B1 health score never trusts prediction's raw_metrics.latency_ms or
   ospf_neighbors. Those have known parsing bugs in Stage 3. B1 uses A4
   beacon data for latency/loss, prediction only for ML risk score.

2. B1 credible floor: beacon OK caps ML damage at 60, DEGRADED at 30.
   This prevents a stale prediction from dragging a healthy router to DOWN.
   Flagged as ML_MISMATCH when triggered.

3. B1 infers HO-Chennai status from senders, because HO is a beacon
   RECEIVER not sender. If any sender reports OK, HO is up.

4. B5 v2 does its OWN correlation by walking the propagation graph.
   B3 marks a router "directly affected" whenever its own signal says so,
   which happens for EVERY router when an upstream interface is
   degraded. B5 finds the true topmost affected ancestor for each.

5. B6 confidence formula = (0.5 + 0.1*depth) * severity_multiplier.
   Depth = propagation hops from root. Severity multiplier:
   CRITICAL=1.0, HIGH=0.85, MEDIUM=0.7, LOW=0.5.

6. B7 scoring: root_router 0.35, root_role 0.15, propagation_depth 0.10,
   affected_routers (Jaccard) 0.20, affected_services (Jaccard) 0.15,
   severity 0.05. Matches below 0.30 dropped.

7. B8 combined confidence = 0.6 * B6_root_confidence
                            + 0.4 * B7_top_match_score

8. B9 reuses predict.py's build_latest_features() by importing it —
   guarantees feature order matches training. If predict.py changes,
   B9 changes automatically.

9. B9 corrects SHAP's log-odds output into probability via sigmoid().
   Cross-check: sigmoid(base_value + shap_sum) must equal model's
   predict_proba() output to <0.001.

10. B10 attaches the ROOT router's SHAP explanation to each incident.
    For cascades, the downstream routers have their own SHAP rows too
    but they're attached to the incident via the root's identity.

───────────────────────────────────────────────────────────────────────────────
8. INTEGRATION WITH OTHER PHASES
───────────────────────────────────────────────────────────────────────────────

Phase A -> Phase B:
  B1 reads beacons/latest_beacon_health.json (A4)
  B1 reads beacons/latest_baselines.json (A3)
  No A-side changes required.

Stage 3 -> Phase B:
  B1 reads ml/inference/latest_prediction.json
  B9 reads ml/models/xgboost_reg_*.pkl + imports predict.py
  No Stage 3 changes required.

Stage 4 (copilot) <- Phase B:
  copilot/run_copilot.py should read (not yet wired):
    rca/latest_rca.json           (root cause, narrative)
    impact/latest_business_impact (INR, RBI penalty)
    explain/latest_explanation    (SHAP grounding)
  This is a Phase C or D integration task.

Stage 5 (dashboard) <- Phase B:
  Dashboard tabs should read (not yet wired):
    analytics/latest_health.json
    impact/latest_impact.json
    impact/latest_business_impact.json
    rca/latest_causal_graph.json   (DOT string for graphviz render)
    rca/latest_rca.json
    explain/latest_explanation.json
  This is a Phase E task.

Phase C (remediation) <- Phase B:
  Decision tree input = rca/latest_rca.json (per-incident root + severity)
  + impact/latest_impact.json (which services are affected)
  + explain/latest_explanation.json (why)

───────────────────────────────────────────────────────────────────────────────
9. TEST STATUS — ALL GREEN
───────────────────────────────────────────────────────────────────────────────

B1  healthy: 94.2 HEALTHY, HO INFERRED, BR-Whitefield ML_MISMATCH     ✓
B1  cascade: 54.2 CRITICAL, floor + safety caps                       ✓
B1  isolated: 86.2 HEALTHY but incident still flagged                 ✓

B2  service_map.json: JSON + structure + all services resolve         ✓

B3  healthy: NONE, 0 routers, 0 services                              ✓
B3  isolated: HIGH, 1 router, 5 services DEGRADED                     ✓
B3  cascade: CRITICAL, 4 routers, 10 services DOWN, 9 RBI             ✓
B3  propagation: HO->ZO->BR-Kora,BR-White chain correct               ✓

B4  healthy: NEGLIGIBLE, ₹0/min, ₹0 RBI penalty                       ✓
B4  isolated: MAJOR, ₹2L/min, ₹9.5L RBI penalty, ₹10L early warning   ✓
B4  cascade: CATASTROPHIC, ₹58.75L/min, ₹25.5L RBI, ₹2.94Cr warning   ✓

B5  healthy: 0 incidents                                              ✓
B5  cascade: 1 incident, CRITICAL, root=HO-Chennai, chain len 4       ✓
B5  isolated: 1 incident, MEDIUM, root=BR-Kora                        ✓
B5  idempotent: same signature across runs                            ✓

B6  healthy: 0 graphs                                                 ✓
B6  cascade: 1 graph, CRITICAL, conf 0.700, 3 active edges            ✓
B6  isolated: 1 graph, MEDIUM, conf 0.350, 0 active edges             ✓
B6  DOT output valid Graphviz                                         ✓

B7  healthy: 0 current incidents                                      ✓
B7  cascade: top match INC-2025-001 (0.85)                            ✓
B7  isolated: top INC-2025-002 (0.85), runner-up INC-2025-003 (0.30)  ✓
B7  --top 5: works                                                    ✓

B8  healthy: system_healthy=True, 0 incidents                         ✓
B8  cascade: 1 CRITICAL, conf 76%                                     ✓
B8  isolated: 1 MEDIUM, conf 55%                                      ✓
B8  narrative generated for both                                      ✓

B9  healthy: method=tree_shap, cross-check OK                         ✓
B9  cascade: SHAP lights up packet_loss / latency correctly           ✓
B9  idempotent: same output across runs                               ✓
B9  log-odds -> probability conversion verified                       ✓

B10 healthy: 4 router explanations, LSTM conflict flagged             ✓
B10 cascade: incident-level explanation attached                      ✓
B10 idempotent: same output across runs                               ✓

No-side-effects verified after every phase:
  beacons/system_check.py → SYSTEM READY
  network/verify/health_check.py → ALL CHECKS PASSED

───────────────────────────────────────────────────────────────────────────────
10. KNOWN LIMITATIONS / NOTES
───────────────────────────────────────────────────────────────────────────────

1. B3's "directly_affected" flags are unreliable when an upstream fault
   affects all downstream routers. B5 v2 compensates by walking the
   propagation graph. Do not trust B3's propagation_chains for RCA;
   always run B5 (which computes them correctly).

2. B6 confidence depends on severity_multiplier * depth. For isolated
   branches (depth 0, MEDIUM), confidence is ~0.35. For cascades
   (depth 2, CRITICAL), ~0.70. This is intentional — depth * severity
   is a real signal. If a demo needs >0.9, we'd need extra signals
   (historical match, incident count).

3. B7's affected_services component scores 0 when the current incident
   has no affected_services list (B6 doesn't carry service IDs).
   The 0.85 max score reflects this. B8 could enrich by pulling services
   from B3 into B6's graphs (future enhancement).

4. B9's SHAP base_value is in LOG-ODDS space, not probability space.
   sigmoid(base_value + shap_sum) = fault_probability. The JSON carries
   log_odds_sum and probability_from_log_odds for clarity.

5. BR-Whitefield LSTM reports 99.8% future fault even on healthy
   networks — this is a Stage 3 training artifact (small dataset, LSTM
   memorized the "BR-Whitefield went bad" pattern). B9/B10 flag it as
   LSTM cross-check "conflicting". Not fixable in Phase B; noted for
   the demo.

6. Phase B is 100% deterministic. No random seeds, no LLM, no external
   calls. Running the same pipeline twice on the same inputs produces
   identical JSON.

───────────────────────────────────────────────────────────────────────────────
11. WHAT'S NEXT — PHASE C PREVIEW
───────────────────────────────────────────────────────────────────────────────

Phase C (Decision & Action) will add:

  C1  incidents/incident_store.py     SQLite persistence
  C2  incidents/incident_engine.py    Lifecycle state machine
                                      (DETECTED -> INVESTIGATING ->
                                       CONFIRMED -> REMEDIATED ->
                                       VERIFIED -> CLOSED)
  C3  audit/audit_logger.py           Append-only JSONL log
  C4  remediation/engine/decision_tree.py   RCA -> action mapping
  C5  remediation/guardrails/rate_limiter.py   1 action/router/5min
  C6  remediation/guardrails/approval_gate.py  Human-in-loop for CRITICAL
  C7  remediation/guardrails/rollback.py       Pre-action state backup
  C8  remediation/actions/restart_bgp.py
  C9  remediation/actions/clear_ospf.py
  C10 remediation/actions/reroute_traffic.py
  C11 remediation/actions/throttle_traffic.py
  C12 remediation/actions/escalate_to_human.py
  C13 remediation/engine/executor.py  Orchestrator with verify + rollback

Phase C reads: rca/latest_rca.json (per-incident root + severity)
             + impact/latest_impact.json (which services affected)
             + explain/latest_explanation.json (why)

No new dependencies. Estimated 13 files.

═══════════════════════════════════════════════════════════════════════════════
END OF PHASE B HANDOFF
Phase B: COMPLETE | Files: 10 | Tests: 100% pass | Ready for Phase C
═══════════════════════════════════════════════════════════════════════════════
