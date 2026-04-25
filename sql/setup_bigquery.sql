-- ============================================================
-- setup_bigquery.sql
-- Run with: bq query --use_legacy_sql=false < sql/setup_bigquery.sql
-- Or paste into the BigQuery console.
-- Replace YOUR_PROJECT_ID with your actual project.
-- ============================================================

-- 1. Create dataset
CREATE SCHEMA IF NOT EXISTS `YOUR_PROJECT_ID.stream_benchmark`
OPTIONS(
  description = "Stream processing architecture benchmark results",
  location = "US"
);

-- ============================================================
-- 2. Shared metrics table (all three architectures write here)
-- ============================================================
CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.stream_benchmark.benchmark_metrics` (
    architecture           STRING    OPTIONS(description="kappa | lambda_stream | lambda_batch | hybrid"),
    window_start           STRING,
    window_end             STRING,
    event_count            INT64,
    avg_latency_ms         FLOAT64,
    max_latency_ms         FLOAT64,
    min_latency_ms         FLOAT64,
    avg_processing_time_ms FLOAT64,
    throughput_per_sec     FLOAT64,
    platform_counts_json   STRING,
    sentiment_counts_json  STRING,
    recorded_at            STRING
)
PARTITION BY DATE(TIMESTAMP(recorded_at))
OPTIONS(
  description = "Per-window benchmark metrics for all architectures",
  require_partition_filter = false
);

-- ============================================================
-- 3. Kappa events table
-- ============================================================
CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.stream_benchmark.kappa_events` (
    event_id            INT64,
    event_uuid          STRING,
    event_timestamp     STRING,
    platform            STRING,
    text                STRING,
    user_id             INT64,
    likes               INT64,
    shares              INT64,
    region              STRING,
    lang                STRING,
    sentiment_label     STRING,
    sentiment_score     FLOAT64,
    word_count          INT64,
    text_hash           STRING,
    architecture        STRING,
    processed_at        STRING,
    latency_ms          FLOAT64,
    processing_time_ms  FLOAT64
)
PARTITION BY DATE(TIMESTAMP(processed_at))
OPTIONS(description = "Kappa architecture — real-time stream events");

-- ============================================================
-- 4. Lambda stream events table
-- ============================================================
CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.stream_benchmark.lambda_events_stream` (
    event_id            INT64,
    event_uuid          STRING,
    event_timestamp     STRING,
    platform            STRING,
    text                STRING,
    user_id             INT64,
    likes               INT64,
    shares              INT64,
    region              STRING,
    lang                STRING,
    sentiment_label     STRING,
    sentiment_score     FLOAT64,
    word_count          INT64,
    text_hash           STRING,
    architecture        STRING,
    path                STRING,
    processed_at        STRING,
    latency_ms          FLOAT64,
    processing_time_ms  FLOAT64
)
PARTITION BY DATE(TIMESTAMP(processed_at))
OPTIONS(description = "Lambda architecture — stream speed layer");

-- ============================================================
-- 5. Lambda batch events table
-- ============================================================
CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.stream_benchmark.lambda_events_batch` (
    event_id            INT64,
    event_uuid          STRING,
    event_timestamp     STRING,
    platform            STRING,
    text                STRING,
    user_id             INT64,
    likes               INT64,
    shares              INT64,
    region              STRING,
    lang                STRING,
    sentiment_label     STRING,
    sentiment_score     FLOAT64,
    word_count          INT64,
    text_hash           STRING,
    architecture        STRING,
    path                STRING,
    processed_at        STRING,
    latency_ms          FLOAT64,
    processing_time_ms  FLOAT64
)
PARTITION BY DATE(TIMESTAMP(processed_at))
OPTIONS(description = "Lambda architecture — batch layer");

-- ============================================================
-- 6. Lambda serving VIEW (merges stream + batch, prefers batch)
-- ============================================================
CREATE OR REPLACE VIEW `YOUR_PROJECT_ID.stream_benchmark.lambda_events_serving` AS
SELECT
    COALESCE(b.event_uuid,       s.event_uuid)       AS event_uuid,
    COALESCE(b.event_id,         s.event_id)         AS event_id,
    COALESCE(b.event_timestamp,  s.event_timestamp)  AS event_timestamp,
    COALESCE(b.platform,         s.platform)         AS platform,
    COALESCE(b.text,             s.text)             AS text,
    COALESCE(b.user_id,          s.user_id)          AS user_id,
    COALESCE(b.sentiment_label,  s.sentiment_label)  AS sentiment_label,
    COALESCE(b.sentiment_score,  s.sentiment_score)  AS sentiment_score,
    COALESCE(b.word_count,       s.word_count)       AS word_count,
    COALESCE(b.latency_ms,       s.latency_ms)       AS latency_ms,
    COALESCE(b.processing_time_ms, s.processing_time_ms) AS processing_time_ms,
    IF(b.event_uuid IS NOT NULL, 'batch', 'stream')  AS serving_path
FROM `YOUR_PROJECT_ID.stream_benchmark.lambda_events_stream` s
FULL OUTER JOIN `YOUR_PROJECT_ID.stream_benchmark.lambda_events_batch` b
    USING (event_uuid);

-- ============================================================
-- 7. Hybrid events table
-- ============================================================
CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.stream_benchmark.hybrid_events` (
    event_id            INT64,
    event_uuid          STRING,
    event_timestamp     STRING,
    platform            STRING,
    text                STRING,
    user_id             INT64,
    likes               INT64,
    shares              INT64,
    region              STRING,
    lang                STRING,
    sentiment_label     STRING,
    sentiment_score     FLOAT64,
    word_count          INT64,
    text_hash           STRING,
    architecture        STRING,
    is_refined          BOOL,
    refined_at          STRING,
    processed_at        STRING,
    latency_ms          FLOAT64,
    processing_time_ms  FLOAT64
)
PARTITION BY DATE(TIMESTAMP(processed_at))
OPTIONS(description = "Hybrid architecture — unified events table");

-- ============================================================
-- 8. Hybrid refinement staging table (temporary, recreated each run)
-- ============================================================
CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.stream_benchmark.hybrid_events_refined_staging` (
    event_id            INT64,
    event_uuid          STRING,
    event_timestamp     STRING,
    platform            STRING,
    text                STRING,
    user_id             INT64,
    likes               INT64,
    shares              INT64,
    region              STRING,
    lang                STRING,
    sentiment_label     STRING,
    sentiment_score     FLOAT64,
    word_count          INT64,
    text_hash           STRING,
    architecture        STRING,
    is_refined          BOOL,
    refined_at          STRING,
    processed_at        STRING,
    latency_ms          FLOAT64,
    processing_time_ms  FLOAT64
)
OPTIONS(description = "Staging table for hybrid batch refinement MERGE");

-- ============================================================
-- 9. Useful analysis queries (run after experiment)
-- ============================================================

-- A. Overall comparison
-- SELECT
--     architecture,
--     COUNT(*)                                AS windows,
--     SUM(event_count)                        AS total_events,
--     ROUND(AVG(avg_latency_ms), 2)           AS avg_latency_ms,
--     ROUND(AVG(throughput_per_sec), 1)       AS avg_throughput,
--     ROUND(AVG(avg_processing_time_ms), 4)   AS avg_proc_time_ms
-- FROM `YOUR_PROJECT_ID.stream_benchmark.benchmark_metrics`
-- GROUP BY architecture
-- ORDER BY architecture;

-- B. Latency percentiles (per architecture)
-- SELECT
--     architecture,
--     APPROX_QUANTILES(avg_latency_ms, 100)[OFFSET(50)]  AS p50_latency_ms,
--     APPROX_QUANTILES(avg_latency_ms, 100)[OFFSET(90)]  AS p90_latency_ms,
--     APPROX_QUANTILES(avg_latency_ms, 100)[OFFSET(99)]  AS p99_latency_ms
-- FROM `YOUR_PROJECT_ID.stream_benchmark.benchmark_metrics`
-- GROUP BY architecture;

-- C. Throughput over time
-- SELECT
--     architecture,
--     window_start,
--     event_count,
--     throughput_per_sec
-- FROM `YOUR_PROJECT_ID.stream_benchmark.benchmark_metrics`
-- ORDER BY architecture, window_start;
