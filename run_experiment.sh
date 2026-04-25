#!/usr/bin/env bash
# run_experiment.sh
# ─────────────────────────────────────────────────────────────
# Runs a single full benchmark experiment for one architecture.
#
# Usage:
#   export PROJECT_ID=your-project-id
#   export REGION=us-central1
#   export BUCKET=your-bucket
#   bash run_experiment.sh kappa   # or lambda | hybrid
# ─────────────────────────────────────────────────────────────
set -euo pipefail

ARCH="${1:?Provide architecture: kappa | lambda | hybrid}"
PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID}"
REGION="${REGION:-us-central1}"
BUCKET="${BUCKET:?Set BUCKET}"
DATASET="stream_benchmark"
TOPIC="stream-benchmark-input"
SUBSCRIPTION="projects/$PROJECT_ID/subscriptions/stream-benchmark-sub"
RATE=200          # messages per second
DURATION=600      # 10 minutes = 600 seconds

echo "============================================="
echo " Running $ARCH benchmark"
echo " Rate    : $RATE msg/s | Duration: ${DURATION}s"
echo "============================================="

COMMON_OPTS=(
  "--project=$PROJECT_ID"
  "--region=$REGION"
  "--bq_dataset=$DATASET"
  "--temp_location=gs://$BUCKET/tmp/$ARCH"
  "--staging_location=gs://$BUCKET/staging/$ARCH"
  "--runner=DataflowRunner"
  "--setup_file=./setup.py"
  "--requirements_file=./requirements.txt"
  "--service_account_email=dataflow-benchmark@${PROJECT_ID}.iam.gserviceaccount.com"
  "--max_num_workers=4"
  "--worker_machine_type=n1-standard-2"
)

# ── 1. Start Dataflow streaming job in background ─────────────
echo "[1/3] Starting Dataflow streaming job..."
case "$ARCH" in
  kappa)
    python src/kappa_pipeline.py \
      "${COMMON_OPTS[@]}" \
      "--subscription=$SUBSCRIPTION" \
      &
    ;;
  lambda)
    python src/lambda_pipeline.py stream \
      "${COMMON_OPTS[@]}" \
      "--subscription=$SUBSCRIPTION" \
      "--gcs_bucket=$BUCKET" \
      &
    ;;
  hybrid)
    python src/hybrid_pipeline.py stream \
      "${COMMON_OPTS[@]}" \
      "--subscription=$SUBSCRIPTION" \
      "--gcs_bucket=$BUCKET" \
      &
    ;;
  *)
    echo "Unknown architecture: $ARCH"
    exit 1
    ;;
esac

DATAFLOW_PID=$!
echo "      Dataflow job started (local PID=$DATAFLOW_PID)."
echo "      Waiting 30s for job to initialise on GCP..."
sleep 30

# ── 2. Run data generator ─────────────────────────────────────
echo "[2/3] Starting data generator ($RATE msg/s for ${DURATION}s)..."
python src/data_generator.py \
  --project "$PROJECT_ID" \
  --topic   "$TOPIC" \
  --rate    "$RATE" \
  --duration "$DURATION"

echo "      Generator finished."

# ── 3. Wait a bit for pipeline to drain, then cancel ─────────
echo "[3/3] Waiting 60s for pipeline to drain remaining messages..."
sleep 60

# Cancel the Dataflow job gracefully
JOB_NAME="${ARCH}-stream-benchmark"
JOB_ID=$(gcloud dataflow jobs list \
  --region="$REGION" \
  --filter="name=$JOB_NAME AND state=Running" \
  --format="value(id)" \
  --limit=1 2>/dev/null || true)

if [[ -n "$JOB_ID" ]]; then
  echo "      Draining Dataflow job $JOB_ID..."
  gcloud dataflow jobs drain "$JOB_ID" --region="$REGION" --quiet
  echo "      Job drain initiated."
fi

# For Lambda: run batch path after stream finishes
if [[ "$ARCH" == "lambda" ]]; then
  echo ""
  echo "── Running Lambda batch path ────────────────"
  python src/lambda_pipeline.py batch \
    "${COMMON_OPTS[@]}" \
    "--gcs_bucket=$BUCKET"
  echo "── Lambda batch complete ────────────────────"
fi

# For Hybrid: run refinement pass
if [[ "$ARCH" == "hybrid" ]]; then
  echo ""
  echo "── Running Hybrid refinement pass ──────────"
  python src/hybrid_pipeline.py refine \
    "${COMMON_OPTS[@]}"
  echo "── Hybrid refinement complete ───────────────"
fi

echo ""
echo "============================================="
echo " $ARCH experiment complete."
echo " Run metrics_logger.py to view results."
echo "============================================="
