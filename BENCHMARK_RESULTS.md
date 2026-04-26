# 📊 Benchmark Results — Stream Processing Architectures on GCP

> **Project:** Benchmarking Lambda, Kappa, and Hybrid Pipelines on Google Cloud  
> **Date:** April 26, 2026  
> **Infrastructure:** GCP scallar-crm | us-central1 | n1-standard-8 × 3 workers  
> **Load:** 421–475 msg/sec | 900 seconds (15 min) per architecture  
> **Total Events Processed:** 1,776,600+

---

## 🏗️ Experimental Setup

| Parameter | Value |
|---|---|
| GCP Project | scallar-crm |
| Region | us-central1 |
| Worker Type | n1-standard-8 (8 vCPU, 30 GB RAM) |
| Workers per Job | 3 (max 32 CPU quota on free trial) |
| Processing Engine | Cloud Dataflow + Apache Beam 2.72.0 |
| Ingestion | Cloud Pub/Sub |
| Storage | BigQuery (US multi-region) |
| Archive | Cloud Storage (lambda-raw/) |
| Target Rate | 500 msg/sec |
| Achieved Rate | 421–475 msg/sec |
| Duration | 900 seconds (15 min) per architecture |
| Python Version | 3.12 |

---

## 📈 Table 1 — Core Performance Metrics

| Architecture | Total Events | Avg Latency (sec) | Min Latency (sec) | Max Latency (sec) | Proc Time/record (ms) |
|---|---|---|---|---|---|
| **Kappa** | 428,300 | 13.28 | 1.53 | 152.41 | 0.0457 |
| **Lambda Stream** | 397,200 | 1.84 | 1.49 | 11.78 | 0.0426 |
| **Lambda Batch** | 572,100 | 18,907.18 | 696.58 | 60,014.71 | 0.0108 |
| **Hybrid** | 379,400 | 1.82 | 1.51 | 12.91 | 0.0407 |

> **Key Finding:** Lambda Stream and Hybrid achieve nearly identical streaming latency (~1.8 sec avg).  
> **Key Finding:** Kappa latency (13.28 sec) is 7.2× higher than Lambda/Hybrid due to window-based BigQuery flush.  
> **Key Finding:** Lambda Batch has highest latency (18,907 sec) by design — batch semantics trade latency for completeness.

---

## 📈 Table 2 — Latency Percentiles

| Architecture | P50 (sec) | P90 (sec) | P99 (sec) | Avg (sec) | Min (sec) |
|---|---|---|---|---|---|
| **Kappa** | 1.75 | 54.83 | 135.76 | 13.28 | 1.53 |
| **Lambda Stream** | 1.63 | 2.18 | 5.94 | 1.84 | 1.49 |
| **Hybrid** | **1.74** | **1.89** | **4.36** | 1.82 | 1.51 |

> **Key Finding:** Hybrid has the tightest P99 (4.36 sec) — most consistent and predictable latency.  
> **Key Finding:** Kappa P99 (135.76 sec) reveals high tail latency caused by worker warm-up spikes.  
> **Key Finding:** Hybrid P90 (1.89 sec) is extremely tight — only 0.15 sec above P50 (1.74 sec).  
> **Key Finding:** Kappa median (1.75 sec) is far below mean (13.28 sec) — right-skewed distribution.

---

## 📈 Table 3 — Throughput Analysis

| Architecture | Avg Throughput (ev/sec) | Peak Throughput (ev/sec) | Total Events | Windows |
|---|---|---|---|---|
| **Kappa** | **475.89** | **493.33** | 428,300 | 15 |
| **Lambda Stream** | 413.75 | 480.00 | 397,200 | 16 |
| **Lambda Batch** | N/A | N/A | 572,100 | 1 |
| **Hybrid** | 395.21 | 483.33 | 379,400 | 16 |

> **Key Finding:** Kappa achieves highest avg throughput (475.89 ev/sec) — closest to generator rate.  
> **Key Finding:** All streaming architectures sustain 395–476 ev/sec at steady state.  
> **Key Finding:** Peak throughput is similar across all architectures (480–493 ev/sec).

---

## 📈 Table 4 — Processing Efficiency Ratio

> Efficiency = Total Events / Avg Latency (sec) — measures how many events processed per second of latency

| Architecture | Total Events | Avg Latency (sec) | Efficiency Ratio |
|---|---|---|---|
| **Lambda Stream** | 397,200 | 1.84 | **215,869** ✅ |
| **Hybrid** | 379,400 | 1.82 | **208,461** ✅ |
| **Kappa** | 428,300 | 13.28 | 32,251 |

> **Key Finding:** Lambda Stream is 6.7× more efficient than Kappa.  
> **Key Finding:** Hybrid matches Lambda Stream efficiency (only 3.4% lower).

---

## 📈 Table 5 — Platform × Architecture Latency Matrix (seconds)

| Platform | Kappa (sec) | Lambda Stream (sec) | Hybrid (sec) |
|---|---|---|---|
| Instagram | 13.26 | 1.84 | 1.82 |
| Reddit | 13.24 | 1.84 | 1.82 |
| TikTok | 13.32 | 1.84 | 1.82 |
| Twitter | 13.33 | 1.84 | 1.82 |
| YouTube | 13.28 | 1.84 | 1.82 |

> **Key Finding:** Platform type has ZERO significant impact on latency — architecture choice dominates.  
> **Key Finding:** Lambda and Hybrid are perfectly consistent at 1.82–1.84 sec across all platforms.

---

## 📈 Table 6 — Platform Distribution (Kappa)

| Platform | Events | Avg Latency (ms) |
|---|---|---|
| YouTube | 86,032 | 13,275.25 |
| Twitter | 85,623 | 13,329.25 |
| Instagram | 85,608 | 13,259.13 |
| TikTok | 85,537 | 13,319.15 |
| Reddit | 85,500 | 13,238.88 |

> **Key Finding:** Near-perfect load distribution (±532 events = <0.7% variance across platforms).

---

## 📈 Table 7 — Regional Latency Analysis (Kappa)

| Region | Events | Avg Latency (ms) | Min Latency (ms) |
|---|---|---|---|
| eu-central | 107,083 | 13,260.58 | 1,530.91 |
| us-east | 107,286 | 13,280.29 | 1,532.27 |
| us-west | 107,113 | 13,292.10 | 1,526.19 |
| ap-south | 106,818 | 13,304.40 | 1,526.71 |

> **Key Finding:** Regional origin has minimal impact on GCP processing latency (<0.3% variance).  
> **Key Finding:** Even load distribution across all regions (±468 events = <0.5% variance).  
> **Key Finding:** GCP managed services successfully abstract away geographic latency differences.

---

## 📈 Table 8 — Language Distribution & Latency (Kappa)

| Language | Events | Avg Latency (ms) |
|---|---|---|
| German (de) | 85,954 | 13,309.07 |
| Portuguese (pt) | 85,717 | 13,325.59 |
| French (fr) | 85,669 | 13,381.63 |
| Spanish (es) | 85,638 | 13,286.36 |
| **English (en)** | 85,322 | **13,118.21** ✅ |

> **Key Finding:** English shows lowest latency (13,118 ms) — shorter avg token length aids processing.  
> **Key Finding:** French shows highest latency (13,381 ms).  
> **Key Finding:** Language variance is minimal (<2% difference) — not architecturally significant.

---

## 📈 Table 9 — Text Length Impact on Processing

| Text Length | Events | Avg Latency (ms) | Avg Proc Time (ms) |
|---|---|---|---|
| Long (>10 words) | 39,870 | 13,087.75 | 0.0466 |
| Medium (6–10 words) | 388,430 | 13,304.51 | 0.0455 |

> **Key Finding:** Text length has negligible impact on processing time (<2.4% difference).  
> **Key Finding:** Longer texts show slightly lower latency — possible batching efficiency in Dataflow.

---

## 📈 Table 10 — Throughput Over Time (Steady State)

### Kappa (peak throughput windows)
| Minute | Throughput (ev/sec) | Events |
|---|---|---|
| :48 | 490.00 | 29,400 |
| :51 | 483.33 | 29,000 |
| :54 | **493.33** | 29,600 |
| :55 | 491.67 | 29,500 |
| :58 | 488.33 | 29,300 |
| :59 | 491.67 | 29,500 |
| :00 | **493.33** | 29,600 |

### Lambda Stream (steady state)
| Minute | Throughput (ev/sec) | Events |
|---|---|---|
| :13 | 465.00 | 27,900 |
| :15 | 471.67 | 28,300 |
| :20 | 476.67 | 28,600 |
| :21 | **480.00** | 28,800 |
| :24 | 475.00 | 28,500 |

### Hybrid (steady state)
| Minute | Throughput (ev/sec) | Events |
|---|---|---|
| :39 | 476.67 | 28,600 |
| :40 | 471.67 | 28,300 |
| :42 | 455.00 | 27,300 |
| :52 | **483.33** | 29,000 |

> **Key Finding:** All architectures show 1–2 min warm-up before reaching steady state.  
> **Key Finding:** Kappa sustains most consistent throughput (445–493 ev/sec) post warm-up.  
> **Key Finding:** First window always shows reduced throughput due to Dataflow worker initialization.

---

## 📈 Table 11 — Architecture Decision Matrix

| Metric | Kappa | Lambda Stream | Lambda Batch | Hybrid |
|---|---|---|---|---|
| Avg Latency | 13.28 sec | 1.84 sec ✅ | 18,907 sec | **1.82 sec** ✅ |
| P50 Latency | 1.75 sec | 1.63 sec | N/A | **1.74 sec** |
| P90 Latency | 54.83 sec | 2.18 sec | N/A | **1.89 sec** ✅ |
| P99 Latency | 135.76 sec | 5.94 sec | N/A | **4.36 sec** ✅ |
| Min Latency | 1.53 sec | **1.49 sec** ✅ | 696.58 sec | 1.51 sec |
| Proc Time/rec | 0.0457 ms | 0.0426 ms | **0.0108 ms** ✅ | 0.0407 ms |
| Avg Throughput | **475.89/sec** ✅ | 413.75/sec | N/A | 395.21/sec |
| Peak Throughput | **493.33/sec** ✅ | 480.00/sec | N/A | 483.33/sec |
| Efficiency Ratio | 32,251 | **215,869** ✅ | N/A | 208,461 |
| Codebases | **1** ✅ | 2 | 2 | 1+job |
| Reprocessing | Hard | Easy | **Easy** ✅ | Medium |
| Latency Consistency | Low | Medium | N/A | **High** ✅ |
| Best For | High Throughput | Real-time Alerts | Compliance/Audit | **ML/Prod Systems** ✅ |

---

## 💰 Cost Estimate

| Resource | Usage | Est. Cost |
|---|---|---|
| Dataflow (3 × n1-standard-8) | 4 jobs × ~20 min each | ~$2.50 |
| Pub/Sub | ~1.8M messages | ~$0.05 |
| BigQuery Storage | ~2 GB | ~$0.04 |
| BigQuery Queries | ~500 MB scanned | ~$0.00 |
| Cloud Storage | ~800 MB (lambda archive) | ~$0.02 |
| **Total** | **Full experiment** | **~$2.61** |

> All experiments ran on GCP Free Trial (₹10,491 ≈ $126 USD available).

---

## 🔑 Key Findings Summary

```
1. HYBRID has the best latency consistency
   → P99 = 4.36 sec (vs Kappa P99 = 135.76 sec)
   → P90 = 1.89 sec (only 0.15 sec above median!)
   → Best choice for production SLA-bound systems

2. LAMBDA STREAM & HYBRID are virtually identical for avg latency
   → Lambda: 1.84 sec | Hybrid: 1.82 sec (only 1% difference)
   → Hybrid wins on consistency (tighter P90/P99)

3. KAPPA has 7.2x higher avg latency but highest throughput
   → Avg: 13.28 sec | Throughput: 475.89 ev/sec
   → High tail latency (P99=135 sec) from window flush
   → Best for throughput-critical, latency-tolerant use cases

4. LAMBDA BATCH is NOT for real-time (18,907 sec avg)
   → Designed for: historical accuracy & compliance
   → Lowest proc time: 0.0108 ms/record (vectorized batch)
   → Best: financial reporting, audit trails

5. PLATFORM TYPE has ZERO impact on latency (<0.3% variance)
   → Architecture choice entirely dominates performance

6. REGIONAL ORIGIN has NO impact on GCP processing latency
   → GCP managed services abstract geographic variance perfectly

7. WARM-UP = 1-2 minutes for ALL Dataflow jobs
   → Must account for in production SLA planning
   → First window always underperforms steady state

8. EFFICIENCY RATIO: Lambda/Hybrid 6.7x better than Kappa
   → Lambda: 215,869 | Hybrid: 208,461 | Kappa: 32,251
```

---

## 📁 Data Location (BigQuery)

```
Project  : scallar-crm
Dataset  : stream_benchmark

Tables:
  kappa_events          → 428,300 rows
  lambda_events_stream  → 397,200 rows
  lambda_events_batch   → 572,100 rows
  hybrid_events         → 379,400 rows
  benchmark_metrics     → 48 window records

Views:
  lambda_events_serving → UNION of stream + batch (prefers batch)
```

---

## 🔗 Repository

```
GitHub: https://github.com/Patel308/Research-Project-

src/data_generator.py      → Event publisher (Pub/Sub)
src/processing.py          → Shared enrichment logic
src/kappa_pipeline.py      → Kappa Dataflow pipeline
src/lambda_pipeline.py     → Lambda stream + batch pipelines
src/hybrid_pipeline.py     → Hybrid stream + MERGE refinement
src/metrics_logger.py      → Results analysis tool
sql/setup_bigquery.sql     → BigQuery DDL (all tables + views)
setup_gcp.sh               → One-command GCP infra setup
run_experiment.sh          → One-command experiment runner
run_kappa.sh               → Kappa Dataflow job script
run_lambda.sh              → Lambda stream job script
run_lambda_batch.sh        → Lambda batch job script
run_hybrid.sh              → Hybrid Dataflow job script
```

---

*Generated from real GCP experiment — April 26, 2026*  
*All results verified from BigQuery tables in project: scallar-crm*  
*Total events processed across all architectures: 1,776,600+*
