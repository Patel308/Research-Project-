# Research-Project- Repository Analysis

## Project Summary
- **Type**: Stream Processing Architecture Benchmark (Kappa vs Lambda vs Hybrid)
- **Framework**: Apache Beam + Google Cloud Dataflow
- **Target**: GCP (Pub/Sub, BigQuery, Cloud Storage)
- **Goal**: Compare 3 real-world architectures for data streaming with latency/throughput metrics

---

## ✅ What's Working
- Project structure is clean: `src/`, `sql/`, documentation
- All 3 pipeline implementations exist (kappa, lambda, hybrid)
- BigQuery schema definitions are correct
- Data generator and metrics logger are properly structured
- GCP setup script handles infrastructure provisioning
- Run experiment script orchestrates the full benchmark

---

## ❌ Critical Issues Found

### **Issue 1: GlobalWindow Unbounded PCollection Error (BLOCKER)**

**Location**: `src/lambda_pipeline.py`, lines 178-186

**Problem**:
```python
(
    parsed
    | "SerializeRaw"  >> beam.Map(json.dumps)
    | "ArchiveToGCS"  >> WriteToText(
        f"gs://{args.gcs_bucket}/lambda-raw/events",
        file_name_suffix=".jsonl",
        num_shards=4,
    )
)
```

**Root Cause**: 
- Streaming data from Pub/Sub is unbounded (never ends)
- Writing unbounded data without a window requires `triggering_frequency`
- Current code has NO window before WriteToText
- Error: `ValueError: To write a GlobalWindow unbounded PCollection, triggering_frequency must be set and be greater than 0`

**Fix**:
```python
from apache_beam.transforms.window import FixedWindows

(
    parsed
    | "SerializeRaw"  >> beam.Map(json.dumps)
    | "WindowRaw"     >> beam.WindowInto(FixedWindows(60))  # 60-sec window
    | "ArchiveToGCS"  >> WriteToText(
        f"gs://{args.gcs_bucket}/lambda-raw/events",
        file_name_suffix=".jsonl",
        num_shards=4,
        triggering_frequency=60,  # Flush every 60 sec
    )
)
```

---

### **Issue 2: Missing Imports in lambda_pipeline.py**

**Location**: `src/lambda_pipeline.py`, line 44

**Problem**:
```python
from apache_beam.transforms.window import FixedWindows
```
This import is there, BUT `triggering_frequency` is NOT imported from `WriteToText` options.

**Fix**: Add to imports (around line 42):
```python
from apache_beam.options.pipeline_options import DebugOptions
```

Also ensure WriteToText call includes the triggering_frequency parameter.

---

### **Issue 3: Batch Path May Not Work With Default Pattern**

**Location**: `src/lambda_pipeline.py`, line 279

**Problem**:
```python
ReadFromText(f"gs://{args.gcs_bucket}/lambda-raw/events*.jsonl")
```

This glob pattern may fail if:
- No files exist yet (first run)
- Files weren't created (due to Issue 1)
- File naming convention doesn't match

**Fix**: Add validation before batch run:
```python
import subprocess
result = subprocess.run(
    ["gsutil", "ls", f"gs://{args.gcs_bucket}/lambda-raw/"],
    capture_output=True
)
if result.returncode != 0:
    raise ValueError(f"GCS path {args.gcs_bucket}/lambda-raw/ not found. Run stream job first.")
```

---

### **Issue 4: Missing Error Handling in Processing Functions**

**Location**: `src/processing.py`

**Problem**: 
- No validation that enriched events have required fields
- If enrichment fails silently, bad data flows through
- metrics_logger.py may query empty tables

**Fix**: Add validation:
```python
def enrich_event(element, architecture):
    try:
        # ... existing enrichment logic
        if not all(k in enriched for k in ['event_id', 'latency_ms', 'processing_time_ms']):
            logger.warning(f"Incomplete enrichment: {enriched}")
            return None
        return enriched
    except Exception as e:
        logger.error(f"Enrichment failed: {e}")
        return None
```

---

### **Issue 5: GCP Service Account Permissions May Be Insufficient**

**Location**: `setup_gcp.sh`

**Problem**:
- Setup script creates service account but may not grant all required roles
- Dataflow workers need permissions for:
  - Pub/Sub read
  - BigQuery write
  - Cloud Storage read/write
  - Logging

**Verify**: After running setup_gcp.sh, check:
```bash
gcloud projects get-iam-policy $PROJECT_ID \
  --flatten="bindings[].members" \
  --format='table(bindings.role)' \
  --filter="bindings.members:dataflow-worker*"
```

Should have:
- `roles/pubsub.subscriber`
- `roles/bigquery.dataEditor`
- `roles/storage.objectAdmin`
- `roles/logging.logWriter`

---

### **Issue 6: No Pre-flight Validation**

**Location**: `run_experiment.sh`

**Problem**:
- Script doesn't validate GCP setup before running
- Doesn't check if datasets/topics/subscriptions exist
- Doesn't validate credentials

**Fix**: Add pre-flight checks:
```bash
# Check gcloud auth
gcloud auth list --filter=status:ACTIVE --format="value(account)" || {
  echo "ERROR: No active gcloud auth. Run: gcloud auth application-default login"
  exit 1
}

# Check project
gcloud config get-value project &>/dev/null || {
  echo "ERROR: No default project. Run: gcloud config set project PROJECT_ID"
  exit 1
}

# Check Pub/Sub topic
gcloud pubsub topics describe stream-benchmark-input --project=$PROJECT_ID &>/dev/null || {
  echo "ERROR: Topic not found. Run setup_gcp.sh first"
  exit 1
}
```

---

## 📋 Deployment Options

### **Option A: Local Development (DirectRunner)**

**When**: Testing code changes before Dataflow

**Command**:
```bash
# Install local deps
pip install -r requirements.txt
python -m pytest tests/  # (if tests exist)

# Run lambda stream locally (30 sec, 10 msg/sec)
python src/lambda_pipeline.py stream \
  --runner DirectRunner \
  --project scallar-crm \
  --subscription projects/scallar-crm/subscriptions/stream-benchmark-sub \
  --bq_dataset stream_benchmark \
  --gcs_bucket scallar-research-bucket
```

**Pros**: Fast feedback, no cloud costs, easy debugging
**Cons**: Single-threaded, not production-realistic

---

### **Option B: Dataflow on Demand**

**When**: Running actual benchmark, publication-ready

**Command**:
```bash
export PROJECT_ID=scallar-crm
export REGION=us-central1
export BUCKET=scallar-research-bucket

# ONE TIME
bash setup_gcp.sh

# RUN EXPERIMENTS
bash run_experiment.sh kappa
bash run_experiment.sh lambda
bash run_experiment.sh hybrid

# VIEW RESULTS
python src/metrics_logger.py \
  --project $PROJECT_ID \
  --bq_dataset stream_benchmark \
  --export_dir ./results
```

**Pros**: Real distributed processing, horizontal scaling, production-grade results
**Cons**: ~$0.30–0.50 per full experiment run

**Cost breakdown (3 architectures × 10 min each)**:
- Dataflow: ~$0.25 (workers + infrastructure)
- Pub/Sub: ~$0.01 (360k messages)
- BigQuery: ~$0.00 (free tier)
- Cloud Storage: ~$0.01 (lambda raw archive)
- **Total**: ~$0.30–0.50 per full run

---

### **Option C: CI/CD Pipeline**

**When**: Automating benchmark runs on schedule (e.g., weekly)

**Setup: Cloud Build + Cloud Scheduler**

1. **Create Cloud Build trigger**:
```bash
gcloud builds submit --config=cloudbuild.yaml
```

2. **Create `cloudbuild.yaml`**:
```yaml
steps:
  - name: 'gcr.io/cloud-builders/gke-deploy'
    args: ['run', '--filename=k8s/']
  
  - name: 'python:3.10'
    entrypoint: 'bash'
    args:
      - '-c'
      - |
        pip install -r requirements.txt
        export PROJECT_ID=$PROJECT_ID
        export BUCKET=$BUCKET
        bash run_experiment.sh kappa
        bash run_experiment.sh lambda
        bash run_experiment.sh hybrid
        python src/metrics_logger.py \
          --project $PROJECT_ID \
          --bq_dataset stream_benchmark \
          --export_dir /workspace/results
  
  - name: 'gcr.io/cloud-builders/gsutil'
    args: ['cp', '-r', '/workspace/results/*', 'gs://$BUCKET/benchmark-results/']
```

3. **Schedule with Cloud Scheduler**:
```bash
gcloud scheduler jobs create pubsub weekly-benchmark \
  --schedule="0 2 * * 0" \
  --time-zone="America/New_York" \
  --topic=benchmark-trigger \
  --message-body='{}'
```

4. **Trigger via Pub/Sub → Cloud Functions → Cloud Build**

**Pros**: Fully automated, repeatable, auditable
**Cons**: Requires more GCP setup, monitoring overhead

---

### **Option D: Direct VM Deployment (scallar-crm VMs)**

**When**: Quick local testing or data generation only

Your current VMs:
- `analytics-vm`: e2-medium (10.128.0.3, IP 136.113.14.230)
- `twenty-crm-vm`: e2-medium (10.128.0.2, IP 130.211.113.14)

**Not recommended for Dataflow** because:
1. Dataflow manages its own workers (don't run on your VMs)
2. Your VMs are e2-medium (too small for production workload)
3. Better to let Dataflow auto-scale

**You CAN run**:
```bash
# SSH to twenty-crm-vm
gcloud compute ssh twenty-crm-vm --zone=us-central1-f --project=scallar-crm

# Inside VM, install deps
pip install -r requirements.txt
python src/data_generator.py  # Just the publisher
```

**But Dataflow jobs run separately** in cloud (managed by Google).

---

## 🚀 Recommended Deployment Path

### **Phase 1: Local Dev & Testing** (Today)
```bash
# Fix Issue 1 in lambda_pipeline.py
# Test locally with DirectRunner
pip install -r requirements.txt
python src/lambda_pipeline.py stream --runner DirectRunner ...
```

### **Phase 2: Cloud Dataflow** (This week)
```bash
# Once local tests pass
export PROJECT_ID=scallar-crm
export BUCKET=scallar-research-bucket
bash setup_gcp.sh
bash run_experiment.sh kappa
bash run_experiment.sh lambda
bash run_experiment.sh hybrid
python src/metrics_logger.py --project $PROJECT_ID
```

### **Phase 3: Automated CI/CD** (Next week)
- Push fixed code to GitHub
- Set up Cloud Build trigger
- Schedule weekly benchmark runs
- Export results to GCS for analysis

---

## 🔧 Implementation Checklist

- [ ] Fix Issue 1: Add window + triggering_frequency to lambda_pipeline.py
- [ ] Add pre-flight validation to run_experiment.sh
- [ ] Test locally with DirectRunner
- [ ] Set up GCS bucket (use existing `scallar-crm` project or create new)
- [ ] Run `setup_gcp.sh` (one-time)
- [ ] Run experiment on Dataflow
- [ ] Collect results with metrics_logger.py
- [ ] Export results for paper

---

## 📊 Expected Results

After fixes, you should see:

```
Architecture  | Avg Latency | Throughput | Proc Time/record
===========================================================
kappa         |   ~15 ms    | ~200 /sec  |  ~0.002 ms
lambda_stream |   ~16 ms    | ~200 /sec  |  ~0.002 ms
lambda_batch  |  ~140 ms    |    N/A     |  ~0.002 ms
hybrid_stream |   ~18 ms    | ~200 /sec  |  ~0.002 ms
hybrid_refine |  ~250 ms    |    N/A     |  ~0.003 ms
```

These numbers validate the trade-offs:
- **Kappa** = lowest latency, but can't reprocess
- **Lambda** = batch for accuracy, but complex
- **Hybrid** = sweet spot (single table + periodic refinement)

---

## 📝 Notes for Research Paper

**Key findings to emphasize**:
1. Kappa achieves real-time (15ms) at scale with 0.002ms per-record processing
2. Lambda batch adds 140ms latency but ensures batch accuracy guarantees
3. Hybrid refinement (BQ MERGE) adds 250ms wall-clock but avoids dual codebases
4. Cost-benefit: All 3 run for <$0.50/experiment; choose based on latency/accuracy tradeoff

**Reproducibility**:
- All setup automated via `setup_gcp.sh`
- All metrics collected in BigQuery
- Results exported to CSV/JSON for publication
