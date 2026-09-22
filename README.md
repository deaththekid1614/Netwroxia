# NETWROXIA

<p align="center">
  <img src="dashboard/assets/logo.jpeg" alt="Netwroxia Logo" width="180">
</p>

<p align="center">
  <b>Autonomous AI NOC Copilot for Banking Networks</b><br>
  <a href="#">IBM Z Datathon 2026</a> | <b>Team Astro_X</b> | Wildcard Entry<br>
  🔒 100% Air-Gapped &nbsp;|&nbsp; ☁️ Zero Cloud Dependency &nbsp;|&nbsp; 🏦 Banking-Grade
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white">
  <img src="https://img.shields.io/badge/PyTorch-EE4C2C?logo=pytorch&logoColor=white">
  <img src="https://img.shields.io/badge/XGBoost-337AB7?logo=xgboost&logoColor=white">
  <img src="https://img.shields.io/badge/Streamlit-FF4B4B?logo=streamlit&logoColor=white">
  <img src="https://img.shields.io/badge/FRRouting-3C3C3C?logo=linux&logoColor=white">
  <img src="https://img.shields.io/badge/InfluxDB-22ADF6?logo=influxdb&logoColor=white">
  <img src="https://img.shields.io/badge/License-MIT-yellow.svg">
</p>

---

## 🎯 The Problem

Bank NOC engineers watch screens waiting for alerts that fire **AFTER** an ATM goes down, **AFTER** a branch loses CBS access, **AFTER** customers are already angry.

| Impact | Stat |
|--------|------|
| 💸 **1 minute downtime** | ₹50 lakh loss (HFT trading) |
| 📜 **RBI mandates** | 99.9% uptime for core banking |
| 🔒 **Air-gap constraint** | Banks CANNOT use cloud AI (RBI/SEBI compliance) |
| 🏧 **ATM networks** | RBI mandates 95%+ uptime, 24/7 |

> **Reactive alerts are too late. We need prediction.**

---

## ✨ The Solution

**Netwroxia** is an autonomous, air-gapped offline AI NOC Copilot that:

1. **🔮 Predicts** network failures **5–10 minutes before impact**
2. **🗣️ Explains** reasoning in natural language + banking terminology
3. **⚡ Auto-remediates** with zero downtime — reroutes traffic before failure
4. **🔐 Operates 100% offline** — no cloud APIs, no internet dependency

### The 3 Questions Netwroxia Answers

| Question | Answer |
|----------|--------|
| **What** is likely to fail next — and when? | XGBoost + LSTM ensemble with Time-to-Impact (TTI) |
| **Why** is risk assessed as elevated? | Mistral 7B explains root cause with RBI context |
| **What corrective action** before SLA breach? | Auto-remediation engine reroutes in <30 seconds |

---

## 🎉 What's New — Full Roadmap Complete

Netwroxia started as a 6-stage pipeline. It's now a **complete enterprise-grade platform** with 5 additional phases layered on top. Nothing was replaced — everything was extended.

| Phase | What it adds | Files | Status |
|-------|--------------|-------|--------|
| **Stages 1-6** | Core network + telemetry + ML + copilot + dashboard + remediation spec | 30+ | ✅ Complete |
| **Phase A** | Beacons + fault library + system readiness checker | 7 | ✅ Complete |
| **Phase B** | Health score + banking impact + RCA + SHAP explainability | 10 | ✅ Complete |
| **Phase C** | Auto-remediation engine (decisions + actions + guardrails + audit) | 15 | ✅ Complete |
| **Phase D** | KPIs + SLA tracking + trends + MLOps monitoring + reports | 10 | ✅ Complete |
| **Phase E** | Combined 13-tab Mission-Control dashboard | 12 | ✅ Complete |

**Total:** 84+ files, 1300+ test assertions, all green.

---

## 🏗️ Full System Architecture

```text
┌─────────────────────────────────────────────────────────────────────────┐
│                         NETWROXIA — FULL STACK                          │
│              Autonomous AI NOC Copilot for Banking Networks             │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │ STAGE 1: SIMULATED BANKING NETWORK                                │  │
│  │ Containerlab + FRRouting — 4-node Tier-1 Indian bank topology     │  │
│  │  HO-Chennai → ZO-Bengaluru → {BR-Koramangala, BR-Whitefield}      │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                    │                                    │
│                                    ▼ exec / ping / docker               │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │ STAGE 2: TELEMETRY PIPELINE                                       │  │
│  │ Telegraf (collector) + InfluxDB 1.8 (time-series DB)              │  │
│  │  Measurements: ping | ospf_neighbors | bgp_peer | docker_*        │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                    │                                    │
│                                    ▼ HTTP query                         │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │ STAGE 3: PREDICTIVE ANALYTICS                                     │  │
│  │ XGBoost (classifier) + LSTM (forecaster) + Isolation Forest       │  │
│  │  Output: latest_prediction.json (fault prob + TTI per router)     │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                    │                                    │
│                                    ▼ JSON feed                          │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │ STAGE 4: OFFLINE LLM COPILOT                                      │  │
│  │ Mistral 7B Q4_K_M + ChromaDB RAG (RBI circulars, runbooks,        │  │
│  │ past incidents). 100% offline, zero cloud.                        │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                    │                                    │
│  ═════════════════════ PHASE A-E EXTENSIONS ═════════════════════════   │
│                                    │                                    │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │ PHASE A: DATA FOUNDATION                                          │  │
│  │  • Beacon heartbeat system (router → HO)                          │  │
│  │  • 14-scenario fault library (tc netem / docker / vtysh)          │  │
│  │  • System readiness checker (OSPF+BGP+InfluxDB)                   │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                    │                                    │
│                                    ▼                                    │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │ PHASE B: INTELLIGENCE LAYER                                       │  │
│  │  • Health score (0-100, per router/region/network)                │  │
│  │  • Banking impact (services affected + ₹ loss + RBI penalty)      │  │
│  │  • RCA engine (correlation + causal graph + historical match)     │  │
│  │  • SHAP explainability (per-feature contribution)                 │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                    │                                    │
│                                    ▼                                    │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │ PHASE C: AUTO-REMEDIATION ENGINE                                  │  │
│  │  • Decision tree (severity + role + fault hint → action)          │  │
│  │  • 5 whitelisted actions: no_op | restart_bgp | clear_ospf |      │  │
│  │    reroute_traffic | escalate_to_human                            │  │
│  │  • Guardrails: rate limiter, approval gate, snapshot rollback     │  │
│  │  • Audit trail (append-only JSONL) + SQLite incident store        │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                    │                                    │
│                                    ▼                                    │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │ PHASE D: ANALYTICS & REPORTING                                    │  │
│  │  • KPIs (MTTD, MTTR, MTBF, Availability)                          │  │
│  │  • SLA tracking (per service + per branch, severity-weighted)     │  │
│  │  • Trend engine (hourly/daily/weekly/monthly buckets)             │  │
│  │  • MLOps (model registry + drift detector + performance tracker)  │  │
│  │  • Reports (daily + weekly + per-incident)                        │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                    │                                    │
│                                    ▼                                    │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │ PHASE E: COMBINED DASHBOARD (app_v2.py)                           │  │
│  │ 13 tabs: Overview | Beacons | Network | Health | Impact |         │  │
│  │ Predictions | RCA | Copilot | Remediation | MLOps | Reports |     │  │
│  │ Audit | Metrics                                                   │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 📁 Project Structure

```text
netwroxia/
│
├── run_pipeline.py                 # One-command legacy pipeline (Stages 1-4)
├── run_full_pipeline.py            # Phase D10: full pipeline runner (A-E)
├── cleanup.py                      # Cleanup script for stale artifacts
├── docker-compose.yml              # InfluxDB 1.8 + Telegraf
├── README.md                       # This file
├── LICENSE                         # MIT License
│
├── network/                        # STAGE 1: Simulated Banking Network
│   ├── containerlab/
│   │   ├── topology.yml            # 4-node Containerlab topology
│   │   └── frr-configs/            # FRRouting configs (LOCKED)
│   ├── traffic-gen/
│   │   ├── inject_faults.py        # Basic fault injection (tc netem)
│   │   └── enhanced_faults.py      # Advanced fault scenarios
│   └── verify/
│       └── health_check.py         # Full network verification
│
├── telemetry/                      # STAGE 2: Telemetry Pipeline
│   ├── telegraf/
│   │   └── telegraf.conf           # Ping + exec + docker plugins
│   ├── influxdb/
│   │   └── init-scripts/init.iql   # DB + retention policy
│   └── update_telegraf_targets.py  # Phase A6: sync ping targets
│
├── ml/                             # STAGE 3: Predictive Analytics
│   ├── data/
│   │   ├── fetch_metrics.py        # Pull from InfluxDB
│   │   ├── feature_engineer.py     # Build X/y matrices
│   │   ├── labels/                 # Ground-truth labels
│   │   ├── processed/              # Feature vectors (.npy + .json)
│   │   └── raw/                    # Raw metric CSVs
│   ├── models/
│   │   ├── train_anomaly.py        # Isolation Forest
│   │   ├── train_ensemble.py       # XGBoost classifier
│   │   ├── train_lstm.py           # LSTM + TTI predictor
│   │   ├── xgboost_*.pkl           # Trained models
│   │   ├── lstm_*.pt               # Trained LSTM
│   │   └── metrics_*.json          # Training metrics
│   └── inference/
│       ├── predict.py              # Real-time inference
│       └── latest_prediction.json  # Latest predictions
│
├── copilot/                        # STAGE 4: Offline LLM Copilot
│   ├── llm/
│   │   ├── download_model.sh       # Mistral 7B Q4 download script
│   │   ├── inference.py            # llama.cpp wrapper
│   │   ├── mistral-*.gguf          # Model file (~4.4GB)
│   │   └── latest_copilot_response.json
│   ├── rag/
│   │   ├── ingest_documents.py     # ChromaDB ingestion
│   │   └── chroma_db/              # Persistent vector store
│   ├── knowledge_base/
│   │   ├── rbi_circulars/          # RBI compliance docs
│   │   ├── runbooks/               # BGP/OSPF troubleshooting
│   │   └── past_incidents/         # Historical incidents (Phase B7)
│   ├── requirements.txt
│   └── run_copilot.py              # Copilot orchestrator (--fast mode)
│
├── beacons/                        # PHASE A: Beacon Heartbeat System
│   ├── beacon_schema.json          # A1 — schema spec
│   ├── beacon_sender.py            # A2 — send heartbeat probes
│   ├── baseline_engine.py          # A3 — rolling RTT baselines
│   ├── beacon_collector.py         # A4 — enrich + score health
│   └── system_check.py             # A4b — readiness gate
│
├── fault_sim/                      # PHASE A: Fault Library
│   ├── scenario_library.py         # A5 — 14 fault scenarios
│   └── active_faults.json          # Currently active faults
│
├── analytics/                      # PHASES B + D: Analytics
│   ├── health_score.py             # B1 — 0-100 network score
│   ├── kpi_calculator.py           # D1 — MTTD/MTTR/MTBF/availability
│   ├── sla_tracker.py              # D2 — per-service SLA compliance
│   └── trend_engine.py             # D3 — hourly/daily/weekly trends
│
├── impact/                         # PHASE B: Banking Impact
│   ├── service_map.json            # B2 — router→service mapping
│   ├── impact_analyzer.py          # B3 — service impact
│   └── business_impact.py          # B4 — ₹ + RBI penalties
│
├── rca/                            # PHASE B: Root Cause Analysis
│   ├── correlation_engine.py       # B5 — group incidents by root
│   ├── causal_graph.py             # B6 — propagation graph
│   ├── historical_matcher.py       # B7 — match past incidents
│   └── rca_output.py               # B8 — consolidated RCA doc
│
├── explain/                        # PHASE B: Explainable AI
│   ├── shap_explainer.py           # B9 — SHAP values
│   └── explanation_output.py       # B10 — plain-English reasoning
│
├── audit/                          # PHASE C: Audit Trail
│   ├── audit_logger.py             # C1 — append-only JSONL
│   └── audit_logs/                 # One file per day
│
├── incidents/                      # PHASE C: Incident Store
│   ├── incident_store.py           # C2 — SQLite persistence
│   ├── incident_engine.py          # C3 — lifecycle state machine
│   ├── incidents.db                # Active incident records
│   └── snapshots/                  # Pre-action snapshots (C7)
│
├── remediation/                    # PHASE C: Auto-Remediation
│   ├── engine/
│   │   ├── decision_tree.py        # C4 — action selection
│   │   ├── executor.py             # C14 — lifecycle driver
│   │   ├── orchestrator.py         # C15 — top-level CLI
│   │   ├── pending_approval.json   # Human approval queue
│   │   └── escalations/            # Escalation records (C13)
│   ├── guardrails/
│   │   ├── rate_limiter.py         # C5 — 1 action/router/5min
│   │   ├── approval_gate.py        # C6 — CRITICAL requires human
│   │   └── rollback.py             # C7 — snapshot manager
│   └── actions/
│       ├── _base.py                # C8 — abstract contract
│       ├── no_op.py                # C9 — monitor only
│       ├── restart_bgp.py          # C10 — clear ip bgp *
│       ├── clear_ospf.py           # C11 — clear ip ospf process
│       ├── reroute_traffic.py      # C12 — apply tc netem qdisc
│       └── escalate_to_human.py    # C13 — write escalation file
│
├── mlops/                          # PHASE D: MLOps Monitoring
│   ├── model_registry.py           # D4 — track all model versions
│   ├── drift_detector.py           # D5 — feature drift detection
│   └── performance_tracker.py      # D6 — precision/recall/F1 + history
│
├── reports/                        # PHASE D: Reports
│   ├── daily_report.py             # D7 — daily NOC snapshot
│   ├── weekly_report.py            # D8 — weekly rollup
│   ├── incident_report.py          # D9 — per-incident deep dive
│   ├── daily/                      # Daily report outputs
│   ├── weekly/                     # Weekly report outputs
│   └── incidents/                  # Incident report outputs
│
├── dashboard/                      # STAGES 5 + PHASE E: Dashboards
│   ├── app.py                      # Legacy 5-tab dashboard
│   ├── app_v2.py                   # Phase E: 13-tab combined dashboard
│   ├── assets/
│   │   └── logo.jpeg               # Netwroxia logo
│   ├── components/                 # Reusable UI (used by both apps)
│   │   ├── alert_card.py           # Prediction cards
│   │   ├── live_feed.py            # NOC event feed
│   │   ├── metric_chart.py         # Plotly time-series
│   │   ├── status_badge.py         # Router status pills
│   │   └── topology_graph.py       # Interactive network map
│   ├── sections/                   # PHASE E: 13 sections
│   │   ├── overview.py             # E2 — executive summary
│   │   ├── beacons.py              # E3 — beacon monitor
│   │   ├── health.py               # E4 — score breakdown
│   │   ├── impact.py               # E5 — banking impact + RBI
│   │   ├── rca.py                  # E6 — root cause view
│   │   ├── remediation.py          # E7 — approvals + actions
│   │   ├── mlops.py                # E8 — models + drift + perf
│   │   ├── reports.py              # E9 — daily/weekly/incident
│   │   └── audit.py                # E10 — filterable audit trail
│   └── utils/
│       ├── influx_client.py        # InfluxDB read client
│       ├── pipeline_runner.py      # Legacy pipeline executor
│       ├── artifact_loader.py      # E1 — cached JSON loader
│       ├── nx_state.py             # E11b — pure state helpers
│       └── theme.py                # E11a — central CSS
│
├── Handoff_files/                  # Documentation
│   ├── NETWROXIA_HANDOFF.md
│   ├── NETWROXIA_STAGE1_HANDOFF.md
│   ├── NETWROXIA_STAGE2_HANDOFF.md
│   ├── NETWROXIA_STAGE3_HANDOFF.md
│   ├── NETWROXIA_STAGE4_HANDOFF.md
│   ├── NETWROXIA_FRONTEND_HANDOFF.md
│   ├── NETWROXIA_PHASE_A_HANDOFF.md
│   ├── NETWROXIA_PHASE_B_HANDOFF.md
│   ├── NETWROXIA_PHASE_C_HANDOFF.md
│   ├── NETWROXIA_PHASE_D_HANDOFF.md
│   └── NETWROXIA_PHASE_E_HANDOFF.md
│
└── docs/                           # Additional documentation
```

---

## 🚀 Quick Start

### Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Docker + Docker Compose | Latest | For InfluxDB + Telegraf |
| Containerlab | ≥0.60 | Linux only (network sim) |
| Python | 3.10+ | With pip |
| RAM | 8GB+ | For Mistral 7B inference |
| Disk | ~6GB free | Model + data + containers |

### Option 1 — Legacy pipeline (Stages 1-4 only)

```bash
cd netwroxia
sudo python3 run_pipeline.py
```

### Option 2 — Full pipeline (Stages 1-4 + Phases A-E)

```bash
cd netwroxia
python3 run_full_pipeline.py run --phases A,B,C,D
```

### Option 3 — Dashboard

```bash
sudo docker-compose up -d

# Legacy 5-tab dashboard
python3 -m streamlit run dashboard/app.py

# OR new 13-tab combined dashboard
python3 -m streamlit run dashboard/app_v2.py
# Open http://localhost:8501
```

---

## 📊 The 6 Stages (Original Core)

### Stage 1: Simulated Banking Network

A 4-node Tier-1 Indian bank network built with Containerlab + FRRouting.

| Node | Role | Loopback |
|------|------|----------|
| **HO-Chennai** | Head Office (Route Reflector) | 10.255.0.1 |
| **ZO-Bengaluru** | Zonal Office | 10.255.0.2 |
| **BR-Koramangala** | Branch (ATM + Teller) | 10.255.0.3 |
| **BR-Whitefield** | Branch (ATM + Teller) | 10.255.0.4 |

**Protocols:** OSPF + iBGP (AS 65001) + MPLS LDP + Route Reflector

### Stage 2: Telemetry Pipeline

| Measurement | Source | Frequency |
|-------------|--------|-----------|
| `ping` | Path health | 10s |
| `ospf_neighbors` | vtysh exec | 30s |
| `bgp_peer` | vtysh exec | 30s |
| `docker_container_cpu` | Docker API | 10s |
| `docker_container_mem` | Docker API | 10s |

### Stage 3: Predictive Analytics Engine

| Model | Type | Purpose | F1 |
|-------|------|---------|-----|
| **XGBoost** | Supervised | Fault detection | ~99.5% |
| **LSTM** | Time-series | Time-to-Impact | 99.4% |
| **Isolation Forest** | Unsupervised | Anomaly baseline | 34.8% |

### Stage 4: Offline LLM Copilot

Mistral 7B Instruct Q4_K_M + ChromaDB RAG. Runs on CPU, zero cloud.

### Stage 5: Streamlit NOC Dashboard (Legacy)

Original 5-tab dashboard. Still fully functional as fallback.

### Stage 6: Auto-Remediation Engine

Originally planned as Stage 6, now fully implemented in **Phase C**.

---

## 🆕 The 5 New Phases (Enterprise Extension)

### Phase A — Data Foundation

| File | Purpose |
|------|---------|
| `beacon_schema.json` | Beacon packet format + thresholds |
| `beacon_sender.py` | Sends heartbeats from each router → HO |
| `baseline_engine.py` | Rolling RTT μ/σ per router |
| `beacon_collector.py` | Consecutive-miss detection + health |
| `system_check.py` | Readiness gate |
| `scenario_library.py` | 14 fault scenarios |
| `update_telegraf_targets.py` | Resync telegraf pings |

### Phase B — Intelligence Layer

| File | Purpose |
|------|---------|
| `health_score.py` | 0-100 score per router/region/network |
| `service_map.json` | Router → banking service mapping |
| `impact_analyzer.py` | Which services are down + chains |
| `business_impact.py` | ₹ loss + projections + RBI penalties |
| `correlation_engine.py` | Group symptoms into incidents |
| `causal_graph.py` | Propagation graph per incident |
| `historical_matcher.py` | Match against past incidents |
| `rca_output.py` | Consolidated RCA + narrative |
| `shap_explainer.py` | Per-feature SHAP contributions |
| `explanation_output.py` | Plain-English reasoning |

### Phase C — Decision & Action (Auto-Remediation)

The missing Stage 6, fully built.

| File | Purpose |
|------|---------|
| `audit_logger.py` | Append-only JSONL, POSIX-locked |
| `incident_store.py` | SQLite incidents + transitions |
| `incident_engine.py` | Lifecycle state machine |
| `decision_tree.py` | (severity + role + hint) → action |
| `rate_limiter.py` | 1 action/router/5min |
| `approval_gate.py` | CRITICAL requires human |
| `rollback.py` | Pre-action snapshot manager |
| `_base.py` | Abstract Action contract |
| `no_op.py` | Monitor only |
| `restart_bgp.py` | `vtysh clear ip bgp *` |
| `clear_ospf.py` | `vtysh clear ip ospf process` |
| `reroute_traffic.py` | `tc netem` qdisc |
| `escalate_to_human.py` | Write escalation file |
| `executor.py` | Full lifecycle driver |
| `orchestrator.py` | Top-level CLI |

**Safety:**
- HO-Chennai never auto-touched without override
- Rate limiter prevents flapping
- CRITICAL always requires approval
- Every action snapshot-reversible
- Every step audit-logged

### Phase D — Analytics & Reporting

| File | Purpose |
|------|---------|
| `kpi_calculator.py` | MTTD, MTTR, MTBF, Availability |
| `sla_tracker.py` | Per-service + per-branch SLA |
| `trend_engine.py` | Hourly/daily/weekly/monthly buckets |
| `model_registry.py` | Scan all trained models |
| `drift_detector.py` | Feature drift vs training |
| `performance_tracker.py` | Precision/recall/F1 + history |
| `daily_report.py` | One-day NOC snapshot |
| `weekly_report.py` | ISO-week rollup + comparison |
| `incident_report.py` | Per-incident deep dive |
| `run_full_pipeline.py` | Unified pipeline runner |

### Phase E — Combined Dashboard

New 13-tab Mission-Control UI. See dashboards section below.

---

## 🖥️ Dashboards

### Legacy Dashboard — `dashboard/app.py` (5 tabs)

| Tab | Features |
|-----|----------|
| 🏠 **Overview** | Router health cards, live event feed, copilot insight |
| 🌐 **Network** | Interactive Plotly topology |
| 🔮 **Predictions** | XGBoost + LSTM alert cards |
| 🤖 **Copilot** | Full structured analysis |
| 📊 **Metrics** | Plotly time-series |

### Combined Dashboard — `dashboard/app_v2.py` (13 tabs)

| Tab | What's inside |
|-----|---------------|
| 🏠 **Overview** | Executive metrics + Router Health + Event Feed + Copilot |
| 📡 **Beacons** | Per-router RTT vs baseline, misses, z-scores |
| 🌐 **Network** | Interactive topology graph |
| 💚 **Health** | Score breakdown with flags |
| 💰 **Impact** | Service impact + ₹ + RBI compliance |
| 🔮 **Predictions** | XGBoost + LSTM alert cards |
| 🧠 **RCA** | Correlation + causal graph + history |
| 🤖 **Copilot** | Full structured analysis |
| 🔧 **Remediation** | Approvals + incident lifecycle + audit |
| 🧪 **MLOps** | Registry + drift + performance |
| 📄 **Reports** | Daily + weekly + incident viewer |
| 📋 **Audit** | Filterable audit trail |
| 📊 **Metrics** | Plotly time-series |

**Run:**
```bash
python3 -m streamlit run dashboard/app_v2.py
```

**Performance note:** autorefresh is disabled by default. Press `R` to refresh manually.

---

## 🧪 Fault Injection

### Basic (Stage 1)

```bash
python3 network/traffic-gen/inject_faults.py latency -l ho-zo -v 100
python3 network/traffic-gen/inject_faults.py reset -l ho-zo
```

### Advanced (Phase A — 14 scenarios)

```bash
python3 fault_sim/scenario_library.py list
python3 fault_sim/scenario_library.py apply latency --link ho-zo --value 150
python3 fault_sim/scenario_library.py apply loss --link zo-ho --value 30
python3 fault_sim/scenario_library.py apply container_pause --container zo-bengaluru
python3 fault_sim/scenario_library.py reset-all
```

**Scenarios:** latency, loss, congestion, jitter, bandwidth_flood, blackhole, interface_down, container_stop, container_pause, bgp_peer_reset, ospf_adjacency_reset, cpu_stress, mem_stress, gradual_degradation

---

## 🤖 LLM Model Setup

```bash
cd copilot/llm
wget https://huggingface.co/TheBloke/Mistral-7B-Instruct-v0.2-GGUF/resolve/main/mistral-7b-instruct-v0.2.Q4_K_M.gguf
```

| Property | Value |
|----------|-------|
| Model | Mistral 7B Instruct v0.2 |
| Quantization | Q4_K_M (~4.4GB) |
| Runtime | llama-cpp-python (CPU-only) |
| Context | 1536 tokens |
| Inference | ~360–470s per router (i5-5250U) |

---

## 📊 Evaluation Metrics

| Dimension | Metric | Value |
|-----------|--------|-------|
| **Technical Merit** | XGBoost precision | ~99.5% |
| | LSTM F1 | 99.4% |
| | False Positive Rate | <4% |
| | Avg Lead Time | 5.2 minutes |
| | Auto-remediation | <30 seconds |
| **Copilot Quality** | Structured JSON | ✅ |
| | RBI compliance context | ✅ |
| | Banking terminology | ✅ |
| **Security** | Cloud dependency | Zero |
| | API keys required | None |
| | Air-gap verified | ✅ |
| **Platform** | Phase A-E tests | 1300+ assertions, 100% pass |
| | Dashboard tabs | 5 (legacy) + 13 (combined) |

---

## 🛠️ Full Tech Stack

| Layer | Tool | Purpose |
|-------|------|---------|
| Network Sim | Containerlab + FRRouting | Simulated bank topology |
| Telemetry | Telegraf + InfluxDB 1.8 | Metric collection + storage |
| ML | XGBoost + PyTorch LSTM | Fault + forecast |
| Anomaly | Scikit-learn Isolation Forest | Baseline detection |
| Explainability | SHAP | Feature contributions |
| LLM | Mistral 7B Q4 (llama.cpp) | Offline analysis |
| RAG | ChromaDB + sentence-transformers | Document retrieval |
| RCA | NetworkX | Causal graphs |
| Storage | SQLite | Incident persistence |
| Audit | JSONL | Immutable event log |
| Reports | Pandas + JSON | Daily/weekly/incident |
| Dashboard | Streamlit + Plotly + NetworkX | Mission-control UI |
| Language | Python 3.10+ | Orchestration |

---

## 📋 Sample Outputs

### Prediction

```text
OVERALL STATUS: SUSPECTED_FAULT
Routers at Risk: 1

Router             XGB Prob   LSTM Future  TTI          Alert
────────────────── ────────── ──────────── ──────────── ────────────────────
HO-Chennai          1.7%       0.2%       5.0 min      NORMAL
ZO-Bengaluru        2.2%       0.6%       5.0 min      NORMAL
BR-Koramangala      1.7%       0.6%       5.0 min      NORMAL
BR-Whitefield      34.7%      99.8%       imminent     SUSPECTED_FAULT
```

### Copilot

```text
🤖 Netwroxia Copilot — Air-Gapped LLM
────────────────────────────────────────
🎯 Predicted Issue: Risk detected on BR-Whitefield
🎯 Root Cause: Increasing packet loss (34.7%) and elevated latency (45.2ms)
🔧 Quick Fix: Switch traffic to backup SD-WAN tunnel before SLA violation.
🛠️ Deep Fix:  Investigate upstream carrier link; validate BGP stability.
⚡ Urgency:    CRITICAL
⏱️  TTI:        4 minutes
🏛️  RBI Note:   SLA breach risk within compliance window.
```

### Remediation Cycle

```text
$ python3 remediation/engine/orchestrator.py cycle

[TICK] 1 RCA incident(s) to process
  [+] created 216bf1a5369b  sev=CRITICAL
══════════════════════════════════════════════════════════════════
 NETWROXIA ORCHESTRATOR — CYCLE (LIVE)
══════════════════════════════════════════════════════════════════
 [TICK] C3 incident engine
   rca_incidents         1
   created               1
   advanced              2
 [RUN] C14 executor
   total                 1
   [OK  ] 216bf1a5369b    stage=verified  action=escalate_to_human
══════════════════════════════════════════════════════════════════
```

### KPI Report

```text
 NETWROXIA — KPI REPORT
──────────────────────────────────────────────────────────────────
 MTTD  mean = 1.50 min (samples=8)
 MTTR  mean = 3.20 min (samples=12)
 MTBF  overall = 12.500 h (gaps=4)
 Availability window = 24 h (99.75% overall, samples=14876)
──────────────────────────────────────────────────────────────────
```

### SLA Compliance

```text
 NETWROXIA — SLA COMPLIANCE (30-day window)
──────────────────────────────────────────────────────────────────
 Services compliant : 10/10  (100.0%)
 Branches compliant : 4/4
──────────────────────────────────────────────────────────────────
 [RBI] cbs_core_banking    target=99.90%  actual=100.00%  margin=+0.10%  [OK]
 [RBI] rtgs_transfers      target=99.90%  actual=100.00%  margin=+0.10%  [OK]
 ...
```

---

## 👥 Team

**Team Astro_X**

| Name | Email |
|------|-------|
| **Prajwal S** | prajwalastronaut@gmail.com |
| **Chaithanya BS** | chaithanyabs441@gmail.com |
| **Karthik Jagadeeschandran** | batkarthik646@gmail.com |


---


## 🙏 Acknowledgements

- [Containerlab](https://containerlab.dev/) — network simulation
- [FRRouting](https://frrouting.org/) — open-source routing stack
- [TheBloke](https://huggingface.co/TheBloke) — quantized LLM models
- [InfluxData](https://www.influxdata.com/) — time-series database
- [Streamlit](https://streamlit.io/) — dashboard framework
- IBM Z Datathon 2026 organizers

---

<p align="center">
  <b>NETWROXIA</b> — Predict · Prevent · Protect<br>
  <sub>Banking Network Copilot · 100% Air-Gapped · Zero Cloud · Feature Complete</sub>
</p>
