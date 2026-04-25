#!/usr/bin/env bash
# setup_gcp.sh
# ─────────────────────────────────────────────────────────────
# ONE-TIME GCP infrastructure setup for the stream benchmark.
# Run this ONCE before deploying any pipelines.
#
# Prerequisites:
#   - gcloud CLI installed and authenticated
#   - Billing enabled on the project
#   - APIs enabled (script enables them automatically)
#
# Usage:
#   export PROJECT_ID=your-project-id
#   export REGION=us-central1
#   export BUCKET=your-unique-bucket-name
#   bash setup_gcp.sh
# ─────────────────────────────────────────────────────────────

set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REGION="${REGION:-us-central1}"
BUCKET="${BUCKET:?Set BUCKET env var}"
DATASET="stream_benchmark"
TOPIC="stream-benchmark-input"
SUBSCRIPTION="stream-benchmark-sub"

echo "============================================="
echo " Stream Benchmark — GCP Setup"
echo " Project : $PROJECT_ID"
echo " Region  : $REGION"
echo " Bucket  : $BUCKET"
echo "============================================="

# ── 1. Set default project ───────────────────────────────────
gcloud config set project "$PROJECT_ID"

# ── 2. Enable required APIs ──────────────────────────────────
echo "[1/7] Enabling GCP APIs..."
gcloud services enable \
  pubsub.googleapis.com \
  dataflow.googleapis.com \
  bigquery.googleapis.com \
  storage.googleapis.com \
  cloudscheduler.googleapis.com \
  --quiet
echo "      APIs enabled."

# ── 3. Create GCS bucket ─────────────────────────────────────
echo "[2/7] Creating GCS bucket..."
if gsutil ls -b "gs://$BUCKET" &>/dev/null; then
  echo "      Bucket gs://$BUCKET already exists — skipping."
else
  gsutil mb -p "$PROJECT_ID" -l "$REGION" -b on "gs://$BUCKET"
  echo "      Bucket gs://$BUCKET created."
fi

# Create sub-directories (GCS objects, so just upload empty markers)
for dir in tmp/kappa tmp/lambda tmp/hybrid staging/kappa staging/lambda staging/hybrid lambda-raw; do
  echo "" | gsutil cp - "gs://$BUCKET/$dir/.keep" 2>/dev/null || true
done
echo "      GCS folder structure created."

# ── 4. Create Pub/Sub topic + subscription ───────────────────
echo "[3/7] Creating Pub/Sub topic and subscription..."
gcloud pubsub topics create "$TOPIC" --quiet 2>/dev/null || echo "      Topic already exists."
gcloud pubsub subscriptions create "$SUBSCRIPTION" \
  --topic="$TOPIC" \
  --ack-deadline=60 \
  --message-retention-duration=1h \
  --quiet 2>/dev/null || echo "      Subscription already exists."
echo "      Pub/Sub ready."

# ── 5. Create BigQuery dataset ───────────────────────────────
echo "[4/7] Creating BigQuery dataset..."
bq mk --dataset \
  --location=US \
  --description="Stream processing architecture benchmark" \
  "${PROJECT_ID}:${DATASET}" 2>/dev/null || echo "      Dataset already exists."
echo "      Dataset $DATASET ready."

# ── 6. Create BigQuery tables ────────────────────────────────
echo "[5/7] Creating BigQuery tables..."
# Replace placeholder in SQL file and run
sed "s/YOUR_PROJECT_ID/$PROJECT_ID/g" sql/setup_bigquery.sql | \
  bq query --use_legacy_sql=false --project_id="$PROJECT_ID"
echo "      Tables created."

# ── 7. Create Dataflow service account ───────────────────────
echo "[6/7] Configuring Dataflow service account..."
SA_NAME="dataflow-benchmark"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud iam service-accounts create "$SA_NAME" \
  --display-name="Dataflow Benchmark SA" \
  --quiet 2>/dev/null || echo "      Service account already exists."

for role in \
  roles/dataflow.worker \
  roles/bigquery.dataEditor \
  roles/bigquery.jobUser \
  roles/pubsub.subscriber \
  roles/storage.objectAdmin; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$SA_EMAIL" \
    --role="$role" \
    --quiet
done
echo "      Service account $SA_EMAIL configured."

# ── 8. Print summary ─────────────────────────────────────────
echo ""
echo "============================================="
echo " Setup Complete!"
echo "============================================="
echo ""
echo " Pub/Sub Topic      : projects/$PROJECT_ID/topics/$TOPIC"
echo " Pub/Sub Sub        : projects/$PROJECT_ID/subscriptions/$SUBSCRIPTION"
echo " GCS Bucket         : gs://$BUCKET"
echo " BigQuery Dataset   : $PROJECT_ID:$DATASET"
echo " Service Account    : $SA_EMAIL"
echo ""
echo " Next steps:"
echo "   1. Install Python deps:  pip install -r requirements.txt"
echo "   2. Run a pipeline:       bash run_experiment.sh"
echo "============================================="
