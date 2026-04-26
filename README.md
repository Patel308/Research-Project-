# Stream Processing Architecture Benchmark on GCP

> **A comparative empirical study of Kappa, Lambda, and Hybrid stream processing architectures using Apache Beam + Google Cloud Dataflow**

[![Python](https://img.shields.io/badge/Python-3.12-blue.svg)](https://python.org)
[![Apache Beam](https://img.shields.io/badge/Apache%20Beam-2.56.0-orange.svg)](https://beam.apache.org)
[![GCP Dataflow](https://img.shields.io/badge/GCP-Dataflow-4285F4.svg)](https://cloud.google.com/dataflow)
[![BigQuery](https://img.shields.io/badge/BigQuery-stream__benchmark-4285F4.svg)](https://cloud.google.com/bigquery)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## Table of Contents

- [Overview](#overview)
- [Research Questions](#research-questions)
- [System Architecture](#system-architecture)
  - [Kappa Architecture](#1-kappa-architecture)
  - [Lambda Architecture](#2-lambda-architecture)
  - [Hybrid Architecture](#3-hybrid-architecture)
- [Data Pipeline](#data-pipeline)
- [GCP Infrastructure](#gcp-infrastructure)
- [Repository Structure](#repository-structure)
- [Benchmark Results](#benchmark-results)
- [Key Findings](#key-findings)
- [Quick Start](#quick-start)
- [Reproducing the Experiment](#reproducing-the-experiment)
- [BigQuery Analysis Queries](#bigquery-analysis-queries)
- [Cost Analysis](#cost-analysis)
- [Discussion](#discussion)

---

## Overview

This project benchmarks three real-world stream processing architectures on Google Cloud Platform, processing **1,776,600+ synthetic social media events** across five platforms (Twitter, Reddit, Instagram, TikTok, YouTube) from four geographic regions.

Each architecture is implemented as a production-grade Apache Beam pipeline deployed on Cloud Dataflow, with shared enrichment logic (sentiment analysis, latency measurement, deduplication hashing) to ensure fair comparison. All results are stored in BigQuery for reproducible analysis.

| Architecture | Description | Codebases | Latency Target |
|---|---|---|---|
| **Kappa** | Single streaming pipeline — process everything in real-time | 1 | Low |
| **Lambda** | Dual path — stream for speed, batch for accuracy | 2 | Mixed |
| **Hybrid** | Single table, periodic BigQuery MERGE refinement | 1 + job | Balanced |

---

## Research Questions

1. **Which architecture achieves the lowest end-to-end latency at scale?**
2. **Which architecture offers the most consistent (low-variance) latency under sustained load?**
3. **How do throughput and latency trade off across architectures?**
4. **Does event source (platform, region, language) affect processing latency?**
5. **What is the operational complexity vs performance benefit of each architecture?**

---

## System Architecture

### Overview Diagram

```
                        ┌─────────────────────────┐
                        │     Data Generator      │
                        │  src/data_generator.py  │
                        │  200–500 msg/sec        │
                        │  Platforms: Twitter,    │
                        │  Reddit, Instagram,     │
                        │  TikTok, YouTube        │
                        └───────────┬─────────────┘
                                    │
                                    ▼
                        ┌─────────────────────────┐
                        │    Cloud Pub/Sub         │
                        │  Topic: stream-benchmark │
                        │  -input                  │
                        │  Sub: stream-benchmark   │
                        │  -sub                    │
                        └─────┬───────┬────────────┘
                              │       │
              ┌───────────────┘       └───────────────┐
              │                                       │
              ▼                                       ▼
  ┌───────────────────┐                 ┌─────────────────────┐
  │  KAPPA Pipeline   │                 │  LAMBDA Pipeline    │
  │  (Architecture 1) │                 │  (Architecture 2)   │
  └────────┬──────────┘                 └──────────┬──────────┘
           │                                       │
           ▼                                       ├──────────────────┐
  ┌────────────────┐                               ▼                  ▼
  │  kappa_events  │                    ┌─────────────────┐  ┌──────────────┐
  │  (BigQuery)    │                    │ lambda_events   │  │  GCS Archive │
  └────────────────┘                   │ _stream (BQ)    │  │ lambda-raw/  │
                                       └─────────────────┘  └──────┬───────┘
                                                                    │
  ┌───────────────────┐                                             ▼
  │  HYBRID Pipeline  │                                   ┌─────────────────┐
  │  (Architecture 3) │                                   │ lambda_events   │
  └────────┬──────────┘                                   │ _batch (BQ)     │
           │                                              └─────────────────┘
           ▼
  ┌─────────────────────┐
  │   hybrid_events     │◄─── Periodic BQ MERGE
  │   (BigQuery)        │     (Refinement Job)
  └─────────────────────┘

              ┌─────────────────────────────────────┐
              │         benchmark_metrics            │
              │         (shared BigQuery table)      │
              │  All architectures write per-window  │
              │  stats here for comparison           │
              └─────────────────────────────────────┘
```

---

### 1. Kappa Architecture

```
                     ┌──────────────────────────────────────────────────┐
                     │              KAPPA PIPELINE                      │
                     │                                                  │
  Pub/Sub ──────────►│  ParseJSON ──► EnrichEvent ──────────────────►  │──► kappa_events (BQ)
  (stream)           │                    │                             │
                     │                    │                             │
                     │               WindowInto ──► GroupByKey ──►     │──► benchmark_metrics (BQ)
                     │             (60 sec fixed)    CalcMetrics        │
                     │                                                  │
                     └──────────────────────────────────────────────────┘

  EnrichEvent adds:
  ├── sentiment_label / sentiment_score (bag-of-words)
  ├── word_count, text_hash (MD5 first 8 chars)
  ├── latency_ms = now() - event.timestamp
  └── processing_time_ms (perf_counter delta)
```

**Key Properties:**
- Single code path — no batch layer
- Streaming inserts directly to BigQuery
- 60-second fixed windows for metrics aggregation
- No reprocessing capability (stateless)
- Highest throughput, highest tail latency

---

### 2. Lambda Architecture

```
                     ┌──────────────────────────────────────────────────────┐
                     │           LAMBDA PIPELINE — STREAM PATH              │
                     │                                                      │
  Pub/Sub ──────────►│  ParseJSON ──► EnrichEvent(path="stream") ────────► │──► lambda_events_stream (BQ)
  (stream)           │      │                                               │
                     │      │                                               │
                     │      └──► WindowInto(60s) ──► WriteToText ─────────►│──► GCS: lambda-raw/*.jsonl
                     │                                                      │
                     └──────────────────────────────────────────────────────┘
                                           │
                                           │ (periodic trigger, e.g. Cloud Scheduler)
                                           ▼
                     ┌──────────────────────────────────────────────────────┐
                     │           LAMBDA PIPELINE — BATCH PATH               │
                     │                                                      │
  GCS Archive ──────►│  ReadFromText ──► ParseGCSLine ──► EnrichEvent ────►│──► lambda_events_batch (BQ)
  (batch)            │                                    (path="batch")   │
                     └──────────────────────────────────────────────────────┘
                                           │
                                           ▼
                     ┌──────────────────────────────────────────────────────┐
                     │           LAMBDA SERVING VIEW                        │
                     │                                                      │
                     │  FULL OUTER JOIN(stream, batch) ON event_uuid        │
                     │  → Prefers batch results when both paths exist       │
                     │  → Falls back to stream for not-yet-batched events   │
                     └──────────────────────────────────────────────────────┘
```

**Key Properties:**
- Two separate codebases (stream + batch)
- Stream path for low-latency reads
- Batch path for high-accuracy, replayable processing
- Serving layer merges via BigQuery VIEW (COALESCE prefers batch)
- Most operationally complex

---

### 3. Hybrid Architecture

```
                     ┌──────────────────────────────────────────────────────┐
                     │           HYBRID PIPELINE — STREAM PATH              │
                     │                                                      │
  Pub/Sub ──────────►│  ParseJSON ──► EnrichEvent ─────────────────────►   │──► hybrid_events (BQ)
  (stream)           │                    │                  is_refined=False│
                     │               WindowInto(60s) ──► CalcMetrics ────► │──► benchmark_metrics (BQ)
                     └──────────────────────────────────────────────────────┘
                                           │
                                           │ (periodic trigger)
                                           ▼
                     ┌──────────────────────────────────────────────────────┐
                     │           HYBRID REFINEMENT JOB (Batch BQ)          │
                     │                                                      │
                     │  SELECT * FROM hybrid_events WHERE NOT is_refined   │
                     │         │                                            │
                     │         ▼                                            │
                     │  [Re-enrich with latest model/rules]                │
                     │         │                                            │
                     │         ▼                                            │
                     │  MERGE hybrid_events                                │
                     │    USING hybrid_events_refined_staging ON event_uuid │
                     │    WHEN MATCHED → UPDATE (set is_refined=True)      │
                     │    WHEN NOT MATCHED → INSERT                        │
                     └──────────────────────────────────────────────────────┘
```

**Key Properties:**
- Single unified table (`hybrid_events`) — no dual codebases
- Stream path writes immediately with `is_refined=False`
- Periodic BQ MERGE job refines results in-place
- Lowest operational complexity among accuracy-capable architectures
- Tightest P99 latency distribution

---

## Data Pipeline

### Event Schema

```json
{
  "event_id": 12345,
  "event_uuid": "a1b2c3d4-...",
  "timestamp": "2026-04-25T14:30:00.123456+00:00",
  "platform": "Twitter",
  "text": "Amazing performance from the team today!",
  "user_id": 67890,
  "likes": 42,
  "shares": 7,
  "region": "us-east",
  "lang": "en"
}
```

### Enrichment Output Schema

```
event_id            INTEGER   Original event ID
event_uuid          STRING    UUID for deduplication
event_timestamp     STRING    Original publish time (ISO 8601)
platform            STRING    Twitter | Reddit | Instagram | TikTok | YouTube
text                STRING    Raw event text
user_id             INTEGER   Simulated user ID
likes               INTEGER   Simulated engagement
shares              INTEGER   Simulated shares
region              STRING    us-east | us-west | eu-central | ap-south
lang                STRING    en | es | fr | de | pt
sentiment_label     STRING    positive | negative | neutral
sentiment_score     FLOAT     [-1.0 ... +1.0]
word_count          INTEGER   Token count
text_hash           STRING    MD5[:8] for deduplication
architecture        STRING    kappa | lambda | hybrid
processed_at        STRING    Worker processing time (ISO 8601)
latency_ms          FLOAT     End-to-end latency in milliseconds
processing_time_ms  FLOAT     Per-record CPU time in milliseconds
```

---

## GCP Infrastructure

```
Project: scallar-crm
Region:  us-central1

┌─────────────────────────────────────────────────────────────┐
│                     GCP SERVICES                            │
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │  Cloud       │  │  Dataflow    │  │   BigQuery       │  │
│  │  Pub/Sub     │  │  (Streaming  │  │  stream_benchmark│  │
│  │              │  │   Engine)    │  │  dataset         │  │
│  │  Topic:      │  │              │  │                  │  │
│  │  stream-     │  │  Workers:    │  │  Tables:         │  │
│  │  benchmark   │  │  n1-std-2    │  │  kappa_events    │  │
│  │  -input      │  │  (auto-      │  │  lambda_events_  │  │
│  │              │  │   scale      │  │  stream/batch    │  │
│  │  Sub:        │  │   1→100)     │  │  hybrid_events   │  │
│  │  stream-     │  │              │  │  benchmark_      │  │
│  │  benchmark   │  │              │  │  metrics         │  │
│  │  -sub        │  │              │  │                  │  │
│  └──────────────┘  └──────────────┘  └──────────────────┘  │
│                                                             │
│  ┌──────────────────────────────────────┐                   │
│  │  Cloud Storage                       │                   │
│  │  scallar-crm-stream-benchmark        │                   │
│  │                                      │                   │
│  │  /tmp/       → Dataflow temp files   │                   │
│  │  /staging/   → Job artifacts         │                   │
│  │  /lambda-raw/→ Raw JSONL archive     │                   │
│  └──────────────────────────────────────┘                   │
└─────────────────────────────────────────────────────────────┘
```

| Resource | Specification |
|---|---|
| GCP Project | `scallar-crm` |
| Region | `us-central1` |
| Worker Machine | `n1-standard-2` (2 vCPU, 7.5 GB RAM) |
| Processing Engine | Cloud Dataflow Streaming Engine |
| Pub/Sub Topic | `stream-benchmark-input` |
| Pub/Sub Subscription | `stream-benchmark-sub` |
| BigQuery Dataset | `stream_benchmark` (US multi-region) |
| GCS Bucket | `scallar-crm-stream-benchmark` |
| Python Version | 3.12 |
| Apache Beam | 2.56.0 |

---

## Repository Structure

```
Research-Project-/
│
├── src/
│   ├── kappa_pipeline.py      # Architecture 1 — Kappa (stream only)
│   ├── lambda_pipeline.py     # Architecture 2 — Lambda (stream + batch)
│   ├── hybrid_pipeline.py     # Architecture 3 — Hybrid (stream + MERGE)
│   ├── data_generator.py      # Pub/Sub event publisher (configurable rate)
│   ├── processing.py          # Shared enrichment logic (all 3 pipelines)
│   └── metrics_logger.py      # Results analysis + CSV export
│
├── sql/
│   └── setup_bigquery.sql     # DDL: all tables, views, analysis queries
│
├── processing.py              # Root-level copy (required by setup.py)
├── setup.py                   # Dataflow worker packaging config
├── requirements.txt           # Python dependencies
│
├── setup_gcp.sh               # One-command GCP infrastructure setup
├── run_experiment.sh          # Orchestrates full benchmark run
├── run_kappa.sh               # Kappa Dataflow job runner
├── run_lambda.sh              # Lambda stream job runner
├── run_lambda_batch.sh        # Lambda batch job runner
├── run_hybrid.sh              # Hybrid Dataflow job runner
│
├── BENCHMARK_RESULTS.md       # Full empirical results with all 11 tables
├── ANALYSIS_AND_FIXES.md      # Technical audit findings and fixes
└── README.md                  # This file
```

---

## Benchmark Results

> **Experiment Parameters**
> - Load: 185–475 msg/sec sustained
> - Duration: 600–900 seconds per architecture
> - Total Events: 1,776,600+ across all architectures
> - Infrastructure: GCP `scallar-crm`, `us-central1`

### Table 1 — Core Performance Metrics

| Architecture | Total Events | Avg Latency | Min Latency | Max Latency | Proc Time/record |
|---|---|---|---|---|---|
| **Kappa** | 428,300 | 13.28 sec | 1.53 sec | 152.41 sec | 0.0457 ms |
| **Lambda Stream** | 397,200 | 1.84 sec | 1.49 sec | 11.78 sec | 0.0426 ms |
| **Lambda Batch** | 572,100 | 18,907 sec | 696 sec | 60,014 sec | 0.0108 ms |
| **Hybrid** | 379,400 | **1.82 sec** | 1.51 sec | 12.91 sec | 0.0407 ms |

### Table 2 — Latency Percentiles (Streaming Architectures)

| Architecture | P50 | P90 | P99 | Mean | Std Dev |
|---|---|---|---|---|---|
| **Kappa** | 1.75 sec | 54.83 sec | 135.76 sec | 13.28 sec | High |
| **Lambda Stream** | 1.63 sec | 2.18 sec | 5.94 sec | 1.84 sec | Low |
| **Hybrid** | **1.74 sec** | **1.89 sec** ✅ | **4.36 sec** ✅ | **1.82 sec** | **Lowest** |

```
Latency Distribution (Conceptual)

Kappa:         |████▌                                    | ← Long right tail
               1s   P50=1.75s        P90=54s   P99=135s

Lambda Stream: |██▌   |                                  | ← Compact
               1s P50=1.63s  P90=2.18s P99=5.94s

Hybrid:        |██▌  |                                   | ← Tightest
               1s P50=1.74s P90=1.89s P99=4.36s
               (P90 only 0.15s above P50!)
```

### Table 3 — Throughput Analysis

| Architecture | Avg Throughput | Peak Throughput | Total Events |
|---|---|---|---|
| **Kappa** | **475.89 ev/sec** ✅ | **493.33 ev/sec** ✅ | 428,300 |
| **Lambda Stream** | 413.75 ev/sec | 480.00 ev/sec | 397,200 |
| **Lambda Batch** | N/A (batch) | N/A | 572,100 |
| **Hybrid** | 395.21 ev/sec | 483.33 ev/sec | 379,400 |

### Table 4 — Architecture Decision Matrix

| Metric | Kappa | Lambda Stream | Lambda Batch | Hybrid |
|---|---|---|---|---|
| Avg Latency | 13.28 sec | 1.84 sec ✅ | 18,907 sec | **1.82 sec** ✅ |
| P90 Latency | 54.83 sec | 2.18 sec | N/A | **1.89 sec** ✅ |
| P99 Latency | 135.76 sec | 5.94 sec | N/A | **4.36 sec** ✅ |
| Avg Throughput | **475.89/s** ✅ | 413.75/s | N/A | 395.21/s |
| Proc Time/rec | 0.0457 ms | 0.0426 ms | **0.0108 ms** ✅ | 0.0407 ms |
| Efficiency Ratio | 32,251 | **215,869** ✅ | N/A | 208,461 |
| Codebases | **1** ✅ | 2 | 2 | **1+job** ✅ |
| Reprocessing | ❌ Hard | ✅ Easy | ✅ Easy | ⚠️ Medium |
| Latency Consistency | Low | Medium | N/A | **High** ✅ |
| Best For | Throughput | Real-time alerts | Compliance/audit | **ML/Prod systems** ✅ |

### Table 5 — Platform Distribution (No Impact on Latency)

| Platform | Events (Kappa) | Avg Latency | % Variance |
|---|---|---|---|
| YouTube | 86,032 | 13,275 ms | baseline |
| Twitter | 85,623 | 13,329 ms | +0.41% |
| Instagram | 85,608 | 13,259 ms | -0.12% |
| TikTok | 85,537 | 13,319 ms | +0.33% |
| Reddit | 85,500 | 13,239 ms | -0.27% |

> **Finding**: Platform type has **zero significant impact** on latency (<0.5% variance). Architecture choice entirely dominates.

### Table 6 — Regional Origin (No Impact on GCP Latency)

| Region | Events | Avg Latency | Δ from Mean |
|---|---|---|---|
| eu-central | 107,083 | 13,260 ms | -0.15% |
| us-east | 107,286 | 13,280 ms | 0.00% |
| us-west | 107,113 | 13,292 ms | +0.09% |
| ap-south | 106,818 | 13,304 ms | +0.18% |

> **Finding**: GCP managed services **perfectly abstract geographic variance**. Regional origin has <0.3% impact.

---

## Key Findings

```
┌─────────────────────────────────────────────────────────────────┐
│                    RESEARCH FINDINGS SUMMARY                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. HYBRID wins on latency CONSISTENCY                          │
│     P99 = 4.36s  (vs Kappa P99 = 135.76s = 31× worse)         │
│     P90 = 1.89s  (only 0.15s above median — ultra-stable)      │
│     → Best for production SLA-bound systems                     │
│                                                                 │
│  2. LAMBDA STREAM ≈ HYBRID for avg latency                      │
│     Lambda: 1.84s | Hybrid: 1.82s (only 1.1% difference)       │
│     Hybrid wins on P90/P99 consistency                          │
│                                                                 │
│  3. KAPPA: highest throughput, worst tail latency               │
│     Avg throughput: 475.89 ev/sec (best)                        │
│     P99: 135.76s (worst — 31× higher than Hybrid P99)          │
│     Caused by: 60s window flush spikes                          │
│                                                                 │
│  4. LAMBDA BATCH: for accuracy, not speed                       │
│     Avg latency: 18,907s — designed for batch semantics         │
│     Lowest proc time: 0.0108ms/record (vectorized batch)        │
│     Use for: compliance, financial audit, historical replay     │
│                                                                 │
│  5. PLATFORM TYPE has ZERO impact on latency (<0.5% variance)  │
│                                                                 │
│  6. REGIONAL ORIGIN has NO impact on GCP processing latency    │
│     GCP Streaming Engine abstracts geographic differences        │
│                                                                 │
│  7. ALL architectures need 1-2 min warm-up                      │
│     First window always underperforms steady state              │
│     Plan for this in production SLA design                      │
│                                                                 │
│  8. EFFICIENCY RATIO: Lambda/Hybrid 6.7× better than Kappa     │
│     Lambda: 215,869 | Hybrid: 208,461 | Kappa: 32,251          │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### When to Choose Each Architecture

```
Decision Tree:

Do you need to reprocess historical data?
├── YES → Lambda or Hybrid
│         Need sub-2s P99 consistency?
│         ├── YES → Hybrid (P99=4.36s, single codebase)
│         └── NO  → Lambda Stream (simpler serving layer)
│
└── NO → Kappa or Hybrid
          Is throughput more important than tail latency?
          ├── YES → Kappa (475 ev/sec, simple)
          └── NO  → Hybrid (1.82s avg, best P99)
```

---

## Quick Start

### Prerequisites

```bash
# Python 3.10+
python --version

# gcloud CLI
gcloud --version

# Install dependencies
pip install -r requirements.txt
```

### 1. GCP Setup (One-time)

```bash
export PROJECT_ID=your-project-id
export REGION=us-central1
export BUCKET=your-unique-bucket-name

gcloud config set project $PROJECT_ID
bash setup_gcp.sh
```

### 2. Run Benchmark

```bash
# Windows (Git Bash) — use absolute paths for setup_file
# Copy setup.py to a path with no spaces first:
mkdir -p C:/beam_setup
cp setup.py C:/beam_setup/setup.py
cp processing.py C:/beam_setup/processing.py

# Kappa
python src/kappa_pipeline.py \
  --project=$PROJECT_ID \
  --region=$REGION \
  --subscription=projects/$PROJECT_ID/subscriptions/stream-benchmark-sub \
  --bq_dataset=stream_benchmark \
  --runner=DataflowRunner \
  --temp_location=gs://$BUCKET/tmp \
  --staging_location=gs://$BUCKET/staging \
  --setup_file=/path/to/setup.py \
  --save_main_session &

# Start data generator once job shows JOB_STATE_RUNNING
python src/data_generator.py \
  --project=$PROJECT_ID \
  --topic=stream-benchmark-input \
  --rate=200 \
  --duration=600

# Drain job after generator finishes
gcloud dataflow jobs drain JOB_ID --region=$REGION --project=$PROJECT_ID
```

### 3. View Results

```bash
bq query --project_id=$PROJECT_ID --use_legacy_sql=false "
SELECT
  architecture,
  COUNT(*) as total_events,
  ROUND(AVG(latency_ms)/1000, 2) as avg_latency_sec,
  ROUND(MIN(latency_ms)/1000, 2) as min_latency_sec,
  ROUND(MAX(latency_ms)/1000, 2) as max_latency_sec
FROM stream_benchmark.kappa_events
GROUP BY architecture
UNION ALL
SELECT architecture, COUNT(*), ROUND(AVG(latency_ms)/1000,2),
       ROUND(MIN(latency_ms)/1000,2), ROUND(MAX(latency_ms)/1000,2)
FROM stream_benchmark.lambda_events_stream GROUP BY architecture
UNION ALL
SELECT architecture, COUNT(*), ROUND(AVG(latency_ms)/1000,2),
       ROUND(MIN(latency_ms)/1000,2), ROUND(MAX(latency_ms)/1000,2)
FROM stream_benchmark.hybrid_events GROUP BY architecture
ORDER BY avg_latency_sec
"
```

---

## Reproducing the Experiment

### Full Experiment Sequence

```bash
# Step 1: Setup (once)
bash setup_gcp.sh

# Step 2: Kappa (10-15 min)
bash run_kappa.sh
# → In parallel terminal: python src/data_generator.py --project=$PROJECT_ID --topic=stream-benchmark-input --rate=200 --duration=600
# → Drain: gcloud dataflow jobs drain KAPPA_JOB_ID --region=us-central1 --project=$PROJECT_ID

# Step 3: Lambda Stream (10-15 min)
bash run_lambda.sh
# → In parallel terminal: python src/data_generator.py ...
# → Drain lambda stream job

# Step 4: Lambda Batch (reads GCS archive, ~5 min)
bash run_lambda_batch.sh

# Step 5: Hybrid (10-15 min)
bash run_hybrid.sh
# → In parallel terminal: python src/data_generator.py ...
# → Drain hybrid job

# Step 6: Export Results
python src/metrics_logger.py \
  --project=$PROJECT_ID \
  --bq_dataset=stream_benchmark \
  --export_dir=./results
```

### Important Notes for Reproducibility

- **Warm-up**: Exclude first 2 minutes of each run from latency calculations
- **Worker count**: Dataflow auto-scales; set `--max_num_workers=3` for consistent results
- **Job names**: Each rerun needs a unique `--job_name` to avoid conflicts
- **Windows**: Use absolute paths without spaces for `--setup_file`
- **processing.py**: Must be co-located with `setup.py` in the `--setup_file` directory

---

## BigQuery Analysis Queries

### Full Comparison Report

```sql
-- Overall architecture comparison
SELECT
  architecture,
  COUNT(*) AS total_events,
  ROUND(AVG(latency_ms), 2) AS avg_latency_ms,
  ROUND(MIN(latency_ms), 2) AS min_latency_ms,
  ROUND(MAX(latency_ms), 2) AS max_latency_ms,
  ROUND(STDDEV(latency_ms), 2) AS stddev_latency_ms,
  ROUND(AVG(processing_time_ms), 4) AS avg_proc_time_ms
FROM stream_benchmark.kappa_events
GROUP BY architecture
UNION ALL
SELECT architecture, COUNT(*), ROUND(AVG(latency_ms),2),
  ROUND(MIN(latency_ms),2), ROUND(MAX(latency_ms),2),
  ROUND(STDDEV(latency_ms),2), ROUND(AVG(processing_time_ms),4)
FROM stream_benchmark.lambda_events_stream GROUP BY architecture
UNION ALL
SELECT architecture, COUNT(*), ROUND(AVG(latency_ms),2),
  ROUND(MIN(latency_ms),2), ROUND(MAX(latency_ms),2),
  ROUND(STDDEV(latency_ms),2), ROUND(AVG(processing_time_ms),4)
FROM stream_benchmark.hybrid_events GROUP BY architecture
ORDER BY avg_latency_ms;
```

### Latency Percentiles

```sql
SELECT
  'kappa' AS architecture,
  ROUND(APPROX_QUANTILES(latency_ms, 100)[OFFSET(50)], 2) AS p50_ms,
  ROUND(APPROX_QUANTILES(latency_ms, 100)[OFFSET(90)], 2) AS p90_ms,
  ROUND(APPROX_QUANTILES(latency_ms, 100)[OFFSET(99)], 2) AS p99_ms
FROM stream_benchmark.kappa_events
UNION ALL
SELECT 'lambda_stream',
  ROUND(APPROX_QUANTILES(latency_ms, 100)[OFFSET(50)], 2),
  ROUND(APPROX_QUANTILES(latency_ms, 100)[OFFSET(90)], 2),
  ROUND(APPROX_QUANTILES(latency_ms, 100)[OFFSET(99)], 2)
FROM stream_benchmark.lambda_events_stream
UNION ALL
SELECT 'hybrid',
  ROUND(APPROX_QUANTILES(latency_ms, 100)[OFFSET(50)], 2),
  ROUND(APPROX_QUANTILES(latency_ms, 100)[OFFSET(90)], 2),
  ROUND(APPROX_QUANTILES(latency_ms, 100)[OFFSET(99)], 2)
FROM stream_benchmark.hybrid_events;
```

### Steady-State Latency (Skip Warm-up)

```sql
-- Kappa steady-state (after first 5 min)
SELECT
  ROUND(AVG(latency_ms), 2) AS steady_state_avg_ms,
  COUNT(*) AS events
FROM stream_benchmark.kappa_events
WHERE PARSE_TIMESTAMP('%Y-%m-%dT%H:%M:%E*SZ', processed_at)
    > TIMESTAMP_ADD(
        (SELECT MIN(PARSE_TIMESTAMP('%Y-%m-%dT%H:%M:%E*SZ', processed_at))
         FROM stream_benchmark.kappa_events),
        INTERVAL 5 MINUTE
      );
```

### Throughput Over Time

```sql
SELECT
  architecture,
  window_start,
  event_count,
  ROUND(throughput_per_sec, 2) AS throughput_per_sec,
  ROUND(avg_latency_ms, 2) AS avg_latency_ms
FROM stream_benchmark.benchmark_metrics
ORDER BY architecture, window_start;
```

### Platform × Architecture Heatmap

```sql
SELECT
  platform,
  ROUND(AVG(CASE WHEN architecture = 'kappa'  THEN latency_ms END), 0) AS kappa_ms,
  ROUND(AVG(CASE WHEN architecture = 'lambda' THEN latency_ms END), 0) AS lambda_ms,
  ROUND(AVG(CASE WHEN architecture = 'hybrid' THEN latency_ms END), 0) AS hybrid_ms
FROM (
  SELECT platform, architecture, latency_ms FROM stream_benchmark.kappa_events
  UNION ALL
  SELECT platform, architecture, latency_ms FROM stream_benchmark.lambda_events_stream
  UNION ALL
  SELECT platform, architecture, latency_ms FROM stream_benchmark.hybrid_events
)
GROUP BY platform
ORDER BY platform;
```

---

## Cost Analysis

| Resource | Usage | Estimated Cost |
|---|---|---|
| Dataflow (n1-standard-2 workers) | 4 jobs × ~20 min each | ~$0.50 |
| Cloud Pub/Sub | ~1.8M messages | ~$0.05 |
| BigQuery Storage | ~2 GB | ~$0.04 |
| BigQuery Queries | ~500 MB scanned | ~$0.00 |
| Cloud Storage | ~800 MB (lambda archive) | ~$0.02 |
| **Total** | **Full experiment** | **~$0.61** |

> All experiments can run on GCP Free Trial credits. Total cost under $1 for full 3-architecture benchmark.

---

## Discussion

### Why Hybrid Wins for Production

The Hybrid architecture's key advantage is **P90 consistency**: at 1.89 seconds, it sits only 0.15 seconds above its median (1.74s). This means your 90th-percentile users experience nearly the same latency as your median user. In contrast, Kappa's P90 (54.83s) is **31× its median** — unacceptable for user-facing SLAs.

### Why Kappa Is Still Relevant

Kappa achieves the highest raw throughput (475.89 ev/sec) and the simplest operational model (one codebase, no batch jobs). For use cases that are **latency-tolerant but throughput-critical** — such as clickstream analytics, log aggregation, or metrics collection — Kappa's simplicity and throughput make it the right choice.

### Lambda Batch Trade-offs

The 18,907-second average latency for Lambda Batch is **by design** — batch processing waits to accumulate data before processing it. The payoff is the lowest per-record processing time (0.0108 ms, 4× faster than streaming) and full reprocessing capability. This makes it ideal for compliance, financial reporting, and ML training data pipelines where latency is irrelevant.

### Geographic Abstraction

One of the most noteworthy findings is that **regional origin has zero measurable impact** on GCP processing latency (<0.3% variance). GCP Streaming Engine's managed autoscaling and Pub/Sub's global distribution successfully abstract away geographic differences — a significant operational advantage over self-managed Kafka/Flink setups.

---

## Dependencies

```
apache-beam[gcp]==2.56.0
google-cloud-pubsub==2.21.1
google-cloud-bigquery==3.21.0
google-cloud-storage==2.16.0
google-auth==2.29.0
```

---

## Data Location (BigQuery)

```
Project  : scallar-crm
Dataset  : stream_benchmark

Tables:
  kappa_events              → 428,300 rows (Kappa architecture)
  lambda_events_stream      → 397,200 rows (Lambda stream path)
  lambda_events_batch       → 572,100 rows (Lambda batch path)
  hybrid_events             → 379,400 rows (Hybrid architecture)
  benchmark_metrics         → 48 window records (all architectures)

Views:
  lambda_events_serving     → UNION of stream + batch (batch preferred)
```

---

## Authors

- **Deepesh Patel** — Scallar IT Solutions | deepeshpatelinfinix@gmail.com
- **Kamlesh Gupta** — Co-author
- **Deepanshu Kumar** — Co-author

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

## Citation

If you use this benchmark in your research, please cite:

```bibtex
@misc{patel2026streambenchmark,
  title   = {Comparative Benchmark of Kappa, Lambda, and Hybrid Stream
             Processing Architectures on Google Cloud Platform},
  author  = {Patel, Deepesh and Gupta, Kamlesh and Kumar, Deepanshu},
  year    = {2026},
  url     = {https://github.com/Patel308/Research-Project-},
  note    = {Apache Beam 2.56.0 on GCP Dataflow, 1.77M+ events processed}
}
```

---

*Experiment conducted on GCP `scallar-crm`, `us-central1`, April 2026.*
*Total events processed across all architectures: 1,776,600+*
*All results reproducible from BigQuery tables in project: `scallar-crm`*
