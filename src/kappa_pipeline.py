"""
kappa_pipeline.py  —  Architecture 1: Kappa (stream only)
----------------------------------------------------------
Pub/Sub ──► Dataflow (Apache Beam streaming) ──► BigQuery

All processing happens in real-time. No batch layer.
Metrics are computed over 60-second fixed windows.

Usage:
    python kappa_pipeline.py \
        --project      YOUR_PROJECT_ID \
        --region       us-central1 \
        --subscription projects/YOUR_PROJECT_ID/subscriptions/stream-benchmark-sub \
        --bq_dataset   stream_benchmark \
        --runner       DataflowRunner \
        --temp_location gs://YOUR_BUCKET/tmp/kappa \
        --staging_location gs://YOUR_BUCKET/staging/kappa
"""

import argparse
import json
import logging
from datetime import datetime, timezone

import apache_beam as beam
from apache_beam import window
from apache_beam.io import ReadFromPubSub, WriteToBigQuery
from apache_beam.io.gcp.bigquery import BigQueryDisposition
from apache_beam.options.pipeline_options import (
    PipelineOptions, StandardOptions, GoogleCloudOptions, SetupOptions
)
from apache_beam.transforms.window import FixedWindows
from apache_beam.transforms import trigger

from processing import enrich_event, build_metrics_record

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ARCHITECTURE = "kappa"
WINDOW_SECONDS = 60

# ---------------------------------------------------------------------------
# BigQuery schema helpers
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# DoFns
# ---------------------------------------------------------------------------
class ParsePubSubMessage(beam.DoFn):
    def process(self, message, *args, **kwargs):
        try:
            payload = json.loads(message.decode("utf-8"))
            yield payload
        except Exception as e:
            logger.warning("Failed to parse message: %s  err=%s", message[:200], e)


class EnrichEvent(beam.DoFn):
    def process(self, element, *args, **kwargs):
        enriched = enrich_event(element, ARCHITECTURE)
        if enriched:
            yield enriched


class ComputeWindowMetrics(beam.DoFn):
    """Aggregate per-window stats into a single metrics record."""

    def process(self, keyed_elements, window=beam.DoFn.WindowParam):
        _, events = keyed_elements

        events = list(events)
        if not events:
            return

        latencies = [e["latency_ms"] for e in events if e.get("latency_ms") is not None]
        proc_times = [e["processing_time_ms"] for e in events if e.get("processing_time_ms") is not None]

        count = len(events)
        win_start = window.start.to_utc_datetime().isoformat()
        win_end   = window.end.to_utc_datetime().isoformat()
        duration_sec = (window.end - window.start) or WINDOW_SECONDS

        platform_counts = {}
        sentiment_counts = {}
        for e in events:
            platform_counts[e.get("platform", "unknown")] = platform_counts.get(e.get("platform", "unknown"), 0) + 1
            sentiment_counts[e.get("sentiment_label", "unknown")] = sentiment_counts.get(e.get("sentiment_label", "unknown"), 0) + 1

        metrics = build_metrics_record(
            architecture=ARCHITECTURE,
            window_start=win_start,
            window_end=win_end,
            event_count=count,
            avg_latency_ms=sum(latencies) / len(latencies) if latencies else 0,
            max_latency_ms=max(latencies) if latencies else 0,
            min_latency_ms=min(latencies) if latencies else 0,
            avg_processing_time_ms=sum(proc_times) / len(proc_times) if proc_times else 0,
            throughput_per_sec=count / float(duration_sec),
            platform_counts=platform_counts,
            sentiment_counts=sentiment_counts,
        )
        yield metrics


# ---------------------------------------------------------------------------
# Pipeline builder
# ---------------------------------------------------------------------------
def run(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--project",       required=True)
    parser.add_argument("--region",        default="us-central1")
    parser.add_argument("--subscription",  required=True,
                        help="Full subscription path: projects/P/subscriptions/S")
    parser.add_argument("--bq_dataset",    default="stream_benchmark")
    parser.add_argument("--temp_location", required=True)
    parser.add_argument("--staging_location", required=True)
    # --runner handled by PipelineOptions directly
    known_args, pipeline_args = parser.parse_known_args(argv)

    options = PipelineOptions(pipeline_args)
    options.view_as(StandardOptions).runner = "DataflowRunner"
    options.view_as(StandardOptions).streaming = True
    options.view_as(StandardOptions).streaming = True
    options.view_as(SetupOptions).save_main_session = True

    gcp = options.view_as(GoogleCloudOptions)
    gcp.project          = known_args.project
    gcp.region           = known_args.region
    gcp.temp_location    = known_args.temp_location
    gcp.staging_location = known_args.staging_location
    gcp.job_name         = "kappa-stream-benchmark"

    events_table  = f"{known_args.project}:{known_args.bq_dataset}.kappa_events"
    metrics_table = f"{known_args.project}:{known_args.bq_dataset}.benchmark_metrics"

    with beam.Pipeline(options=options) as p:

        # 1. Read from Pub/Sub
        raw_messages = (
            p
            | "ReadPubSub" >> ReadFromPubSub(subscription=known_args.subscription)
        )

        # 2. Parse JSON
        parsed = (
            raw_messages
            | "ParseJSON"  >> beam.ParDo(ParsePubSubMessage())
        )

        # 3. Enrich (add sentiment, latency, timestamps)
        enriched = (
            parsed
            | "EnrichEvents" >> beam.ParDo(EnrichEvent())
        )

        # 4. Write enriched events to BigQuery (streaming insert)
        (
            enriched
            | "WriteEvents" >> WriteToBigQuery(
                table=events_table,
                schema=EVENTS_SCHEMA,
                write_disposition=BigQueryDisposition.WRITE_APPEND,
                create_disposition=BigQueryDisposition.CREATE_IF_NEEDED,
                method="STREAMING_INSERTS",
            )
        )

        # 5. Compute per-window metrics
        windowed_metrics = (
            enriched
            | "WindowEvents"  >> beam.WindowInto(FixedWindows(WINDOW_SECONDS))
            | "KeyByConstant" >> beam.Map(lambda e: ("all", e))
            | "GroupByWindow" >> beam.GroupByKey()
            | "CalcMetrics"   >> beam.ParDo(ComputeWindowMetrics())
        )

        # 6. Write metrics to shared BQ table
        (
            windowed_metrics
            | "WriteMetrics" >> WriteToBigQuery(
                table=metrics_table,
                schema=METRICS_SCHEMA,
                write_disposition=BigQueryDisposition.WRITE_APPEND,
                create_disposition=BigQueryDisposition.CREATE_IF_NEEDED,
                method="STREAMING_INSERTS",
            )
        )

    logger.info("Kappa pipeline submitted.")


if __name__ == "__main__":
    run()
