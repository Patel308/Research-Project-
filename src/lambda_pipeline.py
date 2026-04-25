"""
lambda_pipeline.py  —  Architecture 2: Lambda (batch + stream)
--------------------------------------------------------------
Stream path:  Pub/Sub ──► Dataflow ──► BigQuery (lambda_events_stream)
Batch  path:  Cloud Storage ──► BigQuery (lambda_events_batch)  [run separately]
Serving layer: BigQuery VIEW that UNION ALLs both tables

The stream path runs as a continuous Dataflow streaming job.
The batch path is a separate Dataflow batch job triggered periodically
(e.g. via Cloud Scheduler → Cloud Run/Functions → Dataflow).

Usage — stream job:
    python lambda_pipeline.py stream \
        --project      YOUR_PROJECT_ID \
        --subscription projects/YOUR_PROJECT_ID/subscriptions/stream-benchmark-sub \
        --bq_dataset   stream_benchmark \
        --gcs_bucket   YOUR_BUCKET \
        --runner       DataflowRunner \
        --temp_location gs://YOUR_BUCKET/tmp/lambda \
        --staging_location gs://YOUR_BUCKET/staging/lambda

Usage — batch job:
    python lambda_pipeline.py batch \
        --project      YOUR_PROJECT_ID \
        --gcs_bucket   YOUR_BUCKET \
        --bq_dataset   stream_benchmark \
        --runner       DataflowRunner \
        --temp_location gs://YOUR_BUCKET/tmp/lambda \
        --staging_location gs://YOUR_BUCKET/staging/lambda
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone

import apache_beam as beam
from apache_beam.io import ReadFromPubSub, WriteToBigQuery, ReadFromText, WriteToText
from apache_beam.io.gcp.bigquery import BigQueryDisposition
from apache_beam.options.pipeline_options import (
    PipelineOptions, StandardOptions, GoogleCloudOptions, SetupOptions
)
from apache_beam.transforms.window import FixedWindows

from processing import enrich_event, build_metrics_record

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ARCHITECTURE = "lambda"
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
        {"name": "path",                "type": "STRING",  "mode": "NULLABLE"},
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


class EnrichEventWithPath(beam.DoFn):
    def __init__(self, path_label: str):
        self.path_label = path_label

    def process(self, element, *args, **kwargs):
        enriched = enrich_event(element, ARCHITECTURE)
        if enriched:
            enriched["path"] = self.path_label
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
            architecture=f"{ARCHITECTURE}_stream",
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


# ─── Stream path ────────────────────────────────────────────────────────────
def run_stream(args, pipeline_args):
    """Streaming Dataflow job: Pub/Sub → BQ + archive raw to GCS."""
    options = PipelineOptions(pipeline_args)
    options.view_as(StandardOptions).streaming = True
    options.view_as(SetupOptions).save_main_session = True

    gcp = options.view_as(GoogleCloudOptions)
    gcp.project          = args.project
    gcp.region           = args.region
    gcp.temp_location    = args.temp_location
    gcp.staging_location = args.staging_location
    gcp.job_name         = "lambda-stream-benchmark"

    stream_table  = f"{args.project}:{args.bq_dataset}.lambda_events_stream"
    metrics_table = f"{args.project}:{args.bq_dataset}.benchmark_metrics"

    with beam.Pipeline(options=options) as p:
        raw = (
            p
            | "ReadPubSub" >> ReadFromPubSub(subscription=args.subscription)
        )

        parsed = raw | "ParseJSON" >> beam.ParDo(ParsePubSubMessage())

        # FIX: Archive raw JSON lines to GCS with proper windowing
        # Streaming data is unbounded, so we must:
        # 1. Apply a time window (60 sec)
        # 2. Set triggering_frequency to flush files periodically
        (
            parsed
            | "SerializeRaw"  >> beam.Map(json.dumps)
            | "WindowRaw"     >> beam.WindowInto(FixedWindows(WINDOW_SECONDS))
            | "ArchiveToGCS"  >> WriteToText(
                f"gs://{args.gcs_bucket}/lambda-raw/events",
                file_name_suffix=".jsonl",
                num_shards=4,
                triggering_frequency=WINDOW_SECONDS,  # Flush every 60 sec
            )
        )

        enriched = parsed | "EnrichStream" >> beam.ParDo(EnrichEventWithPath("stream"))

        # Write stream events
        enriched | "WriteStreamEvents" >> WriteToBigQuery(
            table=stream_table,
            schema=EVENTS_SCHEMA,
            write_disposition=BigQueryDisposition.WRITE_APPEND,
            create_disposition=BigQueryDisposition.CREATE_IF_NEEDED,
            method="STREAMING_INSERTS",
        )

        # Stream metrics
        (
            enriched
            | "Window"        >> beam.WindowInto(FixedWindows(WINDOW_SECONDS))
            | "KeyByConst"    >> beam.Map(lambda e: ("all", e))
            | "GroupBy"       >> beam.GroupByKey()
            | "CalcMetrics"   >> beam.ParDo(ComputeWindowMetrics())
            | "WriteMetrics"  >> WriteToBigQuery(
                table=metrics_table,
                schema=METRICS_SCHEMA,
                write_disposition=BigQueryDisposition.WRITE_APPEND,
                create_disposition=BigQueryDisposition.CREATE_IF_NEEDED,
                method="STREAMING_INSERTS",
            )
        )


# ─── Batch path ─────────────────────────────────────────────────────────────
class ParseGCSLine(beam.DoFn):
    def process(self, line, *args, **kwargs):
        try:
            yield json.loads(line)
        except Exception as e:
            logger.warning("GCS parse error: %s", e)


class ComputeBatchMetrics(beam.DoFn):
    """Aggregate all batch events into a single metrics record."""
    def process(self, keyed_elements, *args, **kwargs):
        _, events = keyed_elements
        events = list(events)
        if not events:
            return

        latencies  = [e["latency_ms"]         for e in events if e.get("latency_ms") is not None]
        proc_times = [e["processing_time_ms"]  for e in events if e.get("processing_time_ms") is not None]
        count = len(events)

        platform_counts  = {}
        sentiment_counts = {}
        for e in events:
            k = e.get("platform", "unknown")
            platform_counts[k] = platform_counts.get(k, 0) + 1
            s = e.get("sentiment_label", "unknown")
            sentiment_counts[s] = sentiment_counts.get(s, 0) + 1

        now = datetime.now(timezone.utc).isoformat()
        yield build_metrics_record(
            architecture=f"{ARCHITECTURE}_batch",
            window_start=now,
            window_end=now,
            event_count=count,
            avg_latency_ms=sum(latencies) / len(latencies) if latencies else 0,
            max_latency_ms=max(latencies) if latencies else 0,
            min_latency_ms=min(latencies) if latencies else 0,
            avg_processing_time_ms=sum(proc_times) / len(proc_times) if proc_times else 0,
            throughput_per_sec=0,   # batch: total events / wall time — measured externally
            platform_counts=platform_counts,
            sentiment_counts=sentiment_counts,
        )


def run_batch(args, pipeline_args):
    """Batch Dataflow job: GCS archived JSONL → BQ."""
    options = PipelineOptions(pipeline_args)
    options.view_as(SetupOptions).save_main_session = True

    gcp = options.view_as(GoogleCloudOptions)
    gcp.project          = args.project
    gcp.region           = args.region
    gcp.temp_location    = args.temp_location
    gcp.staging_location = args.staging_location
    gcp.job_name         = "lambda-batch-benchmark"

    batch_table   = f"{args.project}:{args.bq_dataset}.lambda_events_batch"
    metrics_table = f"{args.project}:{args.bq_dataset}.benchmark_metrics"

    with beam.Pipeline(options=options) as p:
        parsed = (
            p
            | "ReadGCS"    >> ReadFromText(f"gs://{args.gcs_bucket}/lambda-raw/events*.jsonl")
            | "ParseLines" >> beam.ParDo(ParseGCSLine())
        )

        enriched = parsed | "EnrichBatch" >> beam.ParDo(EnrichEventWithPath("batch"))

        enriched | "WriteBatchEvents" >> WriteToBigQuery(
            table=batch_table,
            schema=EVENTS_SCHEMA,
            write_disposition=BigQueryDisposition.WRITE_APPEND,
            create_disposition=BigQueryDisposition.CREATE_IF_NEEDED,
        )

        (
            enriched
            | "KeyForMetrics"  >> beam.Map(lambda e: ("all", e))
            | "GroupBatch"     >> beam.GroupByKey()
            | "CalcBatchMet"   >> beam.ParDo(ComputeBatchMetrics())
            | "WriteBatchMet"  >> WriteToBigQuery(
                table=metrics_table,
                schema=METRICS_SCHEMA,
                write_disposition=BigQueryDisposition.WRITE_APPEND,
                create_disposition=BigQueryDisposition.CREATE_IF_NEEDED,
            )
        )


# ─── Entry point ────────────────────────────────────────────────────────────
def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("stream", "batch"):
        print("Usage: python lambda_pipeline.py [stream|batch] [options...]")
        sys.exit(1)

    mode = sys.argv[1]
    argv_rest = sys.argv[2:]

    parser = argparse.ArgumentParser()
    parser.add_argument("--project",          required=True)
    parser.add_argument("--region",           default="us-central1")
    parser.add_argument("--bq_dataset",       default="stream_benchmark")
    parser.add_argument("--gcs_bucket",       required=True)
    parser.add_argument("--temp_location",    required=True)
    parser.add_argument("--staging_location", required=True)
    parser.add_argument("--runner",           default="DataflowRunner")
    # Stream-only args
    parser.add_argument("--subscription",     default=None)

    known_args, pipeline_args = parser.parse_known_args(argv_rest)

    if mode == "stream":
        if not known_args.subscription:
            parser.error("--subscription is required for stream mode")
        run_stream(known_args, pipeline_args)
    else:
        run_batch(known_args, pipeline_args)


if __name__ == "__main__":
    main()