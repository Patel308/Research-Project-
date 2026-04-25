"""
metrics_logger.py
-----------------
Post-experiment analysis tool.

After all three pipelines have completed their 10-minute runs, this script:
  1. Queries the benchmark_metrics table in BigQuery
  2. Prints a human-readable comparison table
  3. Exports results to a local CSV and JSON
  4. Optionally writes a summary record back to BQ

Usage:
    python metrics_logger.py \
        --project    YOUR_PROJECT_ID \
        --bq_dataset stream_benchmark \
        --export_dir ./results
"""

import argparse
import csv
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# BigQuery queries
# ---------------------------------------------------------------------------
SUMMARY_QUERY = """
SELECT
    architecture,
    COUNT(*)                                AS window_count,
    SUM(event_count)                        AS total_events,
    ROUND(AVG(avg_latency_ms), 2)           AS avg_latency_ms,
    ROUND(MIN(min_latency_ms), 2)           AS min_latency_ms,
    ROUND(MAX(max_latency_ms), 2)           AS max_latency_ms,
    ROUND(AVG(avg_processing_time_ms), 4)   AS avg_processing_time_ms,
    ROUND(AVG(throughput_per_sec), 1)       AS avg_throughput_per_sec,
    ROUND(MAX(throughput_per_sec), 1)       AS peak_throughput_per_sec,
    MIN(window_start)                       AS experiment_start,
    MAX(window_end)                         AS experiment_end
FROM `{project}.{dataset}.benchmark_metrics`
WHERE recorded_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 2 HOUR)
GROUP BY architecture
ORDER BY architecture
"""

DETAIL_QUERY = """
SELECT
    architecture,
    window_start,
    window_end,
    event_count,
    avg_latency_ms,
    max_latency_ms,
    min_latency_ms,
    avg_processing_time_ms,
    throughput_per_sec,
    platform_counts_json,
    sentiment_counts_json
FROM `{project}.{dataset}.benchmark_metrics`
WHERE recorded_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 2 HOUR)
ORDER BY architecture, window_start
"""


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def _fmt(value, width=14, decimals=2):
    if value is None:
        return "N/A".center(width)
    if isinstance(value, float):
        return f"{value:.{decimals}f}".rjust(width)
    return str(value).rjust(width)


def print_summary_table(rows: list[dict]):
    """Render ASCII comparison table."""
    cols = [
        ("Architecture",    "architecture",           20, 0),
        ("Events",          "total_events",           10, 0),
        ("Avg Lat (ms)",    "avg_latency_ms",         14, 2),
        ("Min Lat (ms)",    "min_latency_ms",         14, 2),
        ("Max Lat (ms)",    "max_latency_ms",         14, 2),
        ("Proc Time (ms)",  "avg_processing_time_ms", 16, 4),
        ("Avg Tput/s",      "avg_throughput_per_sec", 12, 1),
        ("Peak Tput/s",     "peak_throughput_per_sec",12, 1),
    ]

    header = " | ".join(label.ljust(width) for label, _, width, _ in cols)
    sep    = "-+-".join("-" * width for _, _, width, _ in cols)
    print()
    print("=" * (len(header) + 4))
    print("  BENCHMARK RESULTS — ARCHITECTURE COMPARISON")
    print("=" * (len(header) + 4))
    print(header)
    print(sep)

    for row in rows:
        line = " | ".join(
            str(row.get(key, "N/A")).ljust(width) if decimals == 0
            else f"{row.get(key, 0.0):.{decimals}f}".ljust(width)
            for _, key, width, decimals in cols
        )
        print(line)

    print(sep)
    print()


def print_per_window_stats(rows: list[dict]):
    arch_groups = {}
    for row in rows:
        arch = row["architecture"]
        arch_groups.setdefault(arch, []).append(row)

    for arch, windows in sorted(arch_groups.items()):
        print(f"\n── {arch.upper()} — per-window detail ──────────────────────────────")
        print(f"  {'Window Start':<25} {'Count':>8} {'Avg Lat':>10} {'Tput/s':>10}")
        for w in windows:
            print(
                f"  {str(w.get('window_start','')):<25}"
                f" {str(w.get('event_count',''))  :>8}"
                f" {str(w.get('avg_latency_ms','')) :>10}"
                f" {str(w.get('throughput_per_sec','')) :>10}"
            )


def export_results(summary_rows: list[dict], detail_rows: list[dict], export_dir: str):
    Path(export_dir).mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    # CSV summary
    csv_path = os.path.join(export_dir, f"benchmark_summary_{ts}.csv")
    if summary_rows:
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
            writer.writeheader()
            writer.writerows(summary_rows)
        logger.info("Exported summary CSV: %s", csv_path)

    # JSON detail
    json_path = os.path.join(export_dir, f"benchmark_detail_{ts}.json")
    with open(json_path, "w") as f:
        json.dump(
            {"generated_at": ts, "summary": summary_rows, "detail": detail_rows},
            f,
            indent=2,
            default=str,
        )
    logger.info("Exported detail JSON: %s", json_path)

    return csv_path, json_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Query and display benchmark results")
    parser.add_argument("--project",    required=True,            help="GCP project ID")
    parser.add_argument("--bq_dataset", default="stream_benchmark", help="BQ dataset")
    parser.add_argument("--export_dir", default="./results",      help="Local output directory")
    parser.add_argument("--skip_export", action="store_true",     help="Print only, no files")
    args = parser.parse_args()

    try:
        from google.cloud import bigquery
    except ImportError:
        logger.error("google-cloud-bigquery not installed. Run: pip install google-cloud-bigquery")
        return

    client = bigquery.Client(project=args.project)

    logger.info("Querying benchmark_metrics table...")

    # Summary
    summary_job = client.query(SUMMARY_QUERY.format(project=args.project, dataset=args.bq_dataset))
    summary_rows = [dict(row) for row in summary_job.result()]

    if not summary_rows:
        logger.warning("No metrics found. Have the pipelines run yet?")
        print("\nNo data found in benchmark_metrics. Check that at least one pipeline has run.")
        return

    print_summary_table(summary_rows)

    # Detail (per window)
    detail_job = client.query(DETAIL_QUERY.format(project=args.project, dataset=args.bq_dataset))
    detail_rows = [dict(row) for row in detail_job.result()]
    print_per_window_stats(detail_rows)

    if not args.skip_export:
        csv_path, json_path = export_results(summary_rows, detail_rows, args.export_dir)
        print(f"\nResults exported to:\n  {csv_path}\n  {json_path}")

    # Render template paper table
    print("\n" + "=" * 60)
    print("  PAPER TABLE (fill in actual values after experiment)")
    print("=" * 60)
    print(f"{'Architecture':<12} | {'Latency (ms)':>12} | {'Throughput/s':>12} | {'Proc Time (ms)':>14}")
    print("-" * 60)
    for row in summary_rows:
        print(
            f"{str(row.get('architecture','')):<12} | "
            f"{str(row.get('avg_latency_ms','?')):>12} | "
            f"{str(row.get('avg_throughput_per_sec','?')):>12} | "
            f"{str(row.get('avg_processing_time_ms','?')):>14}"
        )
    print("=" * 60)


if __name__ == "__main__":
    main()
