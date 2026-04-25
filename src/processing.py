"""
processing.py
-------------
Shared processing transforms used by all three Dataflow pipelines.
Keeps domain logic in one place so pipelines stay thin.
"""

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dummy sentiment scoring (no external API needed)
# ---------------------------------------------------------------------------
POSITIVE_WORDS = {
    "amazing", "great", "excellent", "good", "love", "perfect", "best",
    "awesome", "fantastic", "wonderful", "happy", "beautiful", "proud",
    "finished", "saved", "breathtaking", "loving", "unbeatable",
}
NEGATIVE_WORDS = {
    "terrible", "worst", "broke", "bad", "hate", "awful", "horrible",
    "broken", "wrong", "failed", "error", "crash", "slow", "late",
}


def score_sentiment(text: str) -> dict:
    """Very simple bag-of-words sentiment — deterministic, no API calls."""
    tokens = set(text.lower().split())
    pos = len(tokens & POSITIVE_WORDS)
    neg = len(tokens & NEGATIVE_WORDS)
    total = pos + neg
    if total == 0:
        label, score = "neutral", 0.0
    elif pos > neg:
        label, score = "positive", round(pos / total, 4)
    elif neg > pos:
        label, score = "negative", round(-neg / total, 4)
    else:
        label, score = "neutral", 0.0
    return {"sentiment_label": label, "sentiment_score": score}


# ---------------------------------------------------------------------------
# Core enrichment — applied identically in all three architectures
# ---------------------------------------------------------------------------
def enrich_event(element: dict, architecture: str) -> dict:
    """
    Add processing metadata to a raw event.
    Returns None if the element is malformed (will be filtered downstream).
    """
    try:
        now_utc = datetime.now(timezone.utc).isoformat()

        # Parse original publish timestamp to compute latency
        event_ts_str = element.get("timestamp", "")
        latency_ms = None
        if event_ts_str:
            try:
                event_ts = datetime.fromisoformat(event_ts_str)
                if event_ts.tzinfo is None:
                    # Assume UTC if no tzinfo
                    from datetime import timezone as _tz
                    event_ts = event_ts.replace(tzinfo=_tz.utc)
                latency_ms = round(
                    (datetime.now(timezone.utc) - event_ts).total_seconds() * 1000, 3
                )
            except Exception:
                latency_ms = None

        # Dummy per-record processing time measurement
        t0 = time.perf_counter()
        sentiment = score_sentiment(element.get("text", ""))
        word_count = len(element.get("text", "").split())
        text_hash = hashlib.md5(element.get("text", "").encode()).hexdigest()[:8]
        processing_time_ms = round((time.perf_counter() - t0) * 1000, 6)

        enriched = {
            # Original fields
            "event_id":        element.get("id"),
            "event_uuid":      element.get("event_uuid", ""),
            "event_timestamp": event_ts_str,
            "platform":        element.get("platform", "unknown"),
            "text":            element.get("text", ""),
            "user_id":         element.get("user_id"),
            "likes":           element.get("likes", 0),
            "shares":          element.get("shares", 0),
            "region":          element.get("region", "unknown"),
            "lang":            element.get("lang", "unknown"),
            # Enrichments
            "sentiment_label":    sentiment["sentiment_label"],
            "sentiment_score":    sentiment["sentiment_score"],
            "word_count":         word_count,
            "text_hash":          text_hash,
            # Processing metadata
            "architecture":       architecture,
            "processed_at":       now_utc,
            "latency_ms":         latency_ms,
            "processing_time_ms": processing_time_ms,
        }
        return enriched

    except Exception as exc:
        logger.error("enrich_event failed for element %s: %s", element, exc)
        return None


# ---------------------------------------------------------------------------
# Metrics record builder
# ---------------------------------------------------------------------------
def build_metrics_record(
    architecture: str,
    window_start: str,
    window_end: str,
    event_count: int,
    avg_latency_ms: float,
    max_latency_ms: float,
    min_latency_ms: float,
    avg_processing_time_ms: float,
    throughput_per_sec: float,
    platform_counts: dict,
    sentiment_counts: dict,
) -> dict:
    return {
        "architecture":           architecture,
        "window_start":           window_start,
        "window_end":             window_end,
        "event_count":            event_count,
        "avg_latency_ms":         round(avg_latency_ms, 3),
        "max_latency_ms":         round(max_latency_ms, 3),
        "min_latency_ms":         round(min_latency_ms, 3),
        "avg_processing_time_ms": round(avg_processing_time_ms, 6),
        "throughput_per_sec":     round(throughput_per_sec, 2),
        "platform_counts_json":   json.dumps(platform_counts),
        "sentiment_counts_json":  json.dumps(sentiment_counts),
        "recorded_at":            datetime.now(timezone.utc).isoformat(),
    }
