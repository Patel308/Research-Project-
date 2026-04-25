"""
hybrid_pipeline.py  —  Architecture 3: Hybrid (stream + periodic batch refinement)
-----------------------------------------------------------------------------------
Stream path:  Pub/Sub ──► Dataflow ──► BigQuery (hybrid_events)
Refinement:   Cloud Scheduler triggers a batch job every N minutes that
              re-scores low-confidence stream results and updates BQ.

The key difference from Lambda:
  • Lambda keeps separate stream + batch tables and merges at query time.
  • Hybrid writes everything to ONE table; batch jobs UPDATE/INSERT records
    in-place (MERGE statement) — so queries always see the "best" version.

Usage — streaming job:
    python hybrid_pipeline.py stream \
        --project      YOUR_PROJECT_ID \
        --subscription projects/P/subscriptions/stream-benchmark-sub \
        --bq_dataset   stream_benchmark \
        --gcs_bucket   YOUR_BUCKET \
        --runner       DataflowRunner \
        --temp_location gs://YOUR_BUCKET/tmp/hybrid \
        --staging_location gs://YOUR_BUCKET/staging/hybrid

Usage — refinement batch job:
    python hybrid_pipeline.py refine \
        --project    YOUR_PROJECT_ID \
        --bq_dataset stream_benchmark \
        --runner     DataflowRunner \
        --temp_location gs://YOUR_BUCKET/tmp/hybrid \
        --staging_location gs://YOUR_BUCKET/staging/hybrid
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone

import apache_beam as beam
from apache_beam.io import ReadFromPubSub, WriteToBigQuery
from apache_beam.io.gcp.bigquery import BigQueryDisposition, ReadFromBigQuery
from apache_beam.options.pipeline_options import (
    PipelineOptions, StandardOptions, GoogleCloudOptions, SetupOptions
)
from apache_beam.transforms.window import FixedWindows

from processing import enrich_event, build_metrics_record

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ARCHITECTURE = "hybrid"
WINDOW_SECONDS = 60

# ─── Schemas ────────────────────────────────────────────────────────────────
EVENTS_SCHEMA = {
    "fields": [
        {"name": "event_id",            "type": "INTEGER", "mode": "NULLABLE"},
        {"name": "event_uuid",          "type": "STRING",  "mode": "NULLABLE"},
        {"name": "event_timestamp",     "type": "STRING",  "mode": "NULLABLE"},
        {"name": "platform",            "type": "STRING",  "mode": "NULLABLE"},
        {"name": "text",                "type": "STRING",  "mode": "NULLABLE"},
        {"name": "user_id",             "type": "INTEGER", "mode": "NULLABLE"},
        {"name": "likes",               "type": "INTEGER", "mode": "NULLABLE"},
        {"name": "shares",              "type": "INTEGER", "mode": "NULLABLE"},
        {"name": "region",              "type": "STRING",  "mode": "NULLABLE"},
        {"name": "lang",                "type": "STRING",  "mode": "NULLABLE"},
        {"name": "sentiment_label",     "type": "STRING",  "mode": "NULLABLE"},
        {"name": "sentiment_score",     "type": "FLOAT",   "mode": "NULLABLE"},
        {"name": "word_count",          "type": "INTEGER", "mode": "NULLABLE"},
        {"name": "text_hash",           "type": "STRING",  "mode": "NULLABLE"},
        {"name": "architecture",        "type": "STRING",  "mode": "NULLABLE"},
        {"name": "is_refined",          "type": "BOOLEAN", "mode": "NULLABLE"},
        {"name": "refined_at",          "type": "STRING",  "mode": "NULLABLE"},
        {"name": "processed_at",        "type": "STRING",  "mode": "NULLABLE"},
        {"name": "latency_ms",          "type": "FLOAT",   "mode": "NULLABLE"},
        {"name": "processing_time_ms",  "type": "FLOAT",   "mode": "NULLABLE"},
    ]
}

METRICS_SCHEMA = {
    "fields": [
        {"name": "architecture",           "type": "STRING",  "mode": "NULLABLE"},
        {"name": "window_start",           "type": "STRING",  "mode": "NULLABLE"},
        {"name": "window_end",             "type": "STRING",  "mode": "NULLABLE"},
        {"name": "event_count",            "type": "INTEGER", "mode": "NULLABLE"},
        {"name": "avg_latency_ms",         "type": "FLOAT",   "mode": "NULLABLE"},
        {"name": "max_latency_ms",         "type": "FLOAT",   "mode": "NULLABLE"},
        {"name": "min_latency_ms",         "type": "FLOAT",   "mode": "NULLABLE"},
        {"name": "avg_processing_time_ms", "type": "FLOAT",   "mode": "NULLABLE"},
        {"name": "throughput_per_sec",     "type": "FLOAT",   "mode": "NULLABLE"},
        {"name": "platform_counts_json",   "type": "STRING",  "mode": "NULLABLE"},
        {"name": "sentiment_counts_json",  "type": "STRING",  "mode": "NULLABLE"},
        {"name": "recorded_at",            "type": "STRING",  "mode": "NULLABLE"},
    ]
}


# ─── DoFns ──────────────────────────────────────────────────────────────────
class ParsePubSubMessage(beam.DoFn):
    def process(self, message, *args, **kwargs):
        try:
            yield json.loads(message.decode("utf-8"))
        except Exception as e:
            logger.warning("Parse error: %s", e)


class EnrichHybridEvent(beam.DoFn):
    def process(self, element, *args, **kwargs):
        enriched = enrich_event(element, ARCHITECTURE)
        if enriched:
            enriched["is_refined"] = False
            enriched["refined_at"] = None
            yield enriched


class ComputeWindowMetrics(beam.DoFn):
    def process(self, keyed_elements, window=beam.DoFn.WindowParam):
        _, events = keyed_elements
        events = list(events)
        if not events:
            return

        latencies  = [e["latency_ms"]         for e in events if e.get("latency_ms") is not None]
        proc_times = [e["processing_time_ms"]  for e in events if e.get("processing_time_ms") is not None]
        count = len(events)
        duration_sec = float(window.end - window.start) or WINDOW_SECONDS

        platform_counts  = {}
        sentiment_counts = {}
        for e in events:
            k = e.get("platform", "unknown")
            platform_counts[k] = platform_counts.get(k, 0) + 1
            s = e.get("sentiment_label", "unknown")
            sentiment_counts[s] = sentiment_counts.get(s, 0) + 1

        yield build_metrics_record(
            architecture=ARCHITECTURE,
            window_start=window.start.to_utc_datetime().isoformat(),
            window_end=window.end.to_utc_datetime().isoformat(),
            event_count=count,
            avg_latency_ms=sum(latencies) / len(latencies) if latencies else 0,
            max_latency_ms=max(latencies) if latencies else 0,
            min_latency_ms=min(latencies) if latencies else 0,
            avg_processing_time_ms=sum(proc_times) / len(proc_times) if proc_times else 0,
            throughput_per_sec=count / duration_sec,
            platform_counts=platform_counts,
            sentiment_counts=sentiment_counts,
        )


# ─── Refinement batch job DoFns ─────────────────────────────────────────────
class RefineRecord(beam.DoFn):
    """
    Re-applies processing to records flagged is_refined=False.
    In a real system this might call a higher-accuracy ML model.
    Here we re-score sentiment and mark the record as refined.
    """
    def process(self, element, *args, **kwargs):
        from processing import score_sentiment
        import time

        t0 = time.perf_counter()
        sentiment = score_sentiment(element.get("text", ""))
        proc_time = (time.perf_counter() - t0) * 1000

        element["sentiment_label"]     = sentiment["sentiment_label"]
        element["sentiment_score"]     = sentiment["sentiment_score"]
        element["processing_time_ms"]  = round(proc_time, 6)
        element["is_refined"]          = True
        element["refined_at"]          = datetime.now(timezone.utc).isoformat()
        yield element


# ─── Stream path ────────────────────────────────────────────────────────────
def run_stream(args, pipeline_args):
    options = PipelineOptions(pipeline_args)
    options.view_as(StandardOptions).runner = "DataflowRunner"
    options.view_as(StandardOptions).streaming = True
    options.view_as(SetupOptions).save_main_session = True

    gcp = options.view_as(GoogleCloudOptions)
    gcp.project          = args.project
    gcp.region           = args.region
    gcp.temp_location    = args.temp_location
    gcp.staging_location = args.staging_location
    gcp.job_name         = "hybrid-stream-benchmark"

    events_table  = f"{args.project}:{args.bq_dataset}.hybrid_events"
    metrics_table = f"{args.project}:{args.bq_dataset}.benchmark_metrics"

    with beam.Pipeline(options=options) as p:
        raw = p | "ReadPubSub" >> ReadFromPubSub(subscription=args.subscription)

        parsed   = raw      | "ParseJSON"    >> beam.ParDo(ParsePubSubMessage())
        enriched = parsed   | "EnrichEvents" >> beam.ParDo(EnrichHybridEvent())

        enriched | "WriteEvents" >> WriteToBigQuery(
            table=events_table,
            schema=EVENTS_SCHEMA,
            write_disposition=BigQueryDisposition.WRITE_APPEND,
            create_disposition=BigQueryDisposition.CREATE_IF_NEEDED,
            method="STREAMING_INSERTS",
        )

        (
            enriched
            | "Window"       >> beam.WindowInto(FixedWindows(WINDOW_SECONDS))
            | "KeyConst"     >> beam.Map(lambda e: ("all", e))
            | "GroupBy"      >> beam.GroupByKey()
            | "CalcMetrics"  >> beam.ParDo(ComputeWindowMetrics())
            | "WriteMetrics" >> WriteToBigQuery(
                table=metrics_table,
                schema=METRICS_SCHEMA,
                write_disposition=BigQueryDisposition.WRITE_APPEND,
                create_disposition=BigQueryDisposition.CREATE_IF_NEEDED,
                method="STREAMING_INSERTS",
            )
        )


# ─── Refinement batch job ────────────────────────────────────────────────────
def run_refine(args, pipeline_args):
    """
    Read un-refined records from BQ, re-process, MERGE back.
    Because Dataflow doesn't support BQ UPDATE directly, we write
    refined records to a staging table and run a BQ MERGE via the API.
    """
    options = PipelineOptions(pipeline_args)
    options.view_as(StandardOptions).runner = "DataflowRunner"
    options.view_as(SetupOptions).save_main_session = True

    gcp = options.view_as(GoogleCloudOptions)
    gcp.project          = args.project
    gcp.region           = args.region
    gcp.temp_location    = args.temp_location
    gcp.staging_location = args.staging_location
    gcp.job_name         = "hybrid-refine-benchmark"

    staging_table = f"{args.project}:{args.bq_dataset}.hybrid_events_refined_staging"

    query = f"""
        SELECT *
        FROM `{args.project}.{args.bq_dataset}.hybrid_events`
        WHERE is_refined = FALSE
        LIMIT 500000
    """

    with beam.Pipeline(options=options) as p:
        unrefined = (
            p
            | "ReadUnrefined" >> ReadFromBigQuery(query=query, use_standard_sql=True)
        )

        refined = unrefined | "RefineRecords" >> beam.ParDo(RefineRecord())

        refined | "WriteStagingTable" >> WriteToBigQuery(
            table=staging_table,
            schema=EVENTS_SCHEMA,
            write_disposition=BigQueryDisposition.WRITE_TRUNCATE,
            create_disposition=BigQueryDisposition.CREATE_IF_NEEDED,
        )

    # After Dataflow job completes, run BQ MERGE to update main table
    _run_bq_merge(args.project, args.bq_dataset)


def _run_bq_merge(project_id: str, dataset: str):
    """Execute a BigQuery MERGE to apply refined records to the main table."""
    from google.cloud import bigquery

    client = bigquery.Client(project=project_id)
    merge_sql = f"""
        MERGE `{project_id}.{dataset}.hybrid_events` T
        USING `{project_id}.{dataset}.hybrid_events_refined_staging` S
        ON T.event_uuid = S.event_uuid
        WHEN MATCHED THEN UPDATE SET
            T.sentiment_label     = S.sentiment_label,
            T.sentiment_score     = S.sentiment_score,
            T.processing_time_ms  = S.processing_time_ms,
            T.is_refined          = TRUE,
            T.refined_at          = S.refined_at
    """
    logger.info("Running BQ MERGE for hybrid refinement...")
    query_job = client.query(merge_sql)
    result = query_job.result()
    logger.info("MERGE complete. Rows affected: %s", query_job.num_dml_affected_rows)


# ─── Entry point ────────────────────────────────────────────────────────────
def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("stream", "refine"):
        print("Usage: python hybrid_pipeline.py [stream|refine] [options...]")
        sys.exit(1)

    mode = sys.argv[1]
    argv_rest = sys.argv[2:]

    parser = argparse.ArgumentParser()
    parser.add_argument("--project",          required=True)
    parser.add_argument("--region",           default="us-central1")
    parser.add_argument("--bq_dataset",       default="stream_benchmark")
    parser.add_argument("--gcs_bucket",       default=None)
    parser.add_argument("--temp_location",    required=True)
    parser.add_argument("--staging_location", required=True)
    # --runner handled by PipelineOptions directly
    parser.add_argument("--subscription",     default=None)

    known_args, pipeline_args = parser.parse_known_args(argv_rest)

    if mode == "stream":
        if not known_args.subscription:
            parser.error("--subscription required for stream mode")
        run_stream(known_args, pipeline_args)
    else:
        run_refine(known_args, pipeline_args)


if __name__ == "__main__":
    main()
