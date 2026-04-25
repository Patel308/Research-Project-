"""
data_generator.py
-----------------
Simulates a real-time social media data stream and publishes JSON messages
to a Google Cloud Pub/Sub topic at a configurable rate (100-500 msg/sec).

Usage:
    python data_generator.py --project YOUR_PROJECT_ID \
                              --topic stream-benchmark-input \
                              --rate 200 \
                              --duration 600
"""

import argparse
import json
import random
import time
import uuid
import logging
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

from google.cloud import pubsub_v1

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Static sample data
# ---------------------------------------------------------------------------
PLATFORMS = ["twitter", "youtube", "instagram", "reddit", "tiktok"]

SAMPLE_TEXTS = [
    "Just had an amazing coffee this morning! #blessed",
    "The new software update completely broke my workflow.",
    "Watched the sunset from the hill — absolutely breathtaking.",
    "Traffic is terrible today, going to be late again.",
    "Finally finished my project after weeks of hard work!",
    "Not sure how I feel about the new UI changes.",
    "Great customer service experience at the store today.",
    "This product is terrible, worst purchase ever.",
    "The weather is perfect for a run outside.",
    "Loving the new features in the latest release!",
    "Stream processing is the future of data pipelines.",
    "Batch jobs are so last decade — switch to streaming.",
    "Hybrid architectures give you the best of both worlds.",
    "Latency matters more than anything in real-time systems.",
    "Pub/Sub + Dataflow is an unbeatable combination on GCP.",
    "BigQuery handles petabytes without breaking a sweat.",
    "Apache Beam makes writing portable pipelines a breeze.",
    "Data quality is the foundation of good analytics.",
    "Monitoring and alerting saved us from a major outage.",
    "The team shipped a zero-downtime migration — proud moment.",
]

HASHTAGS = [
    "#cloudcomputing", "#bigdata", "#streaming", "#dataengineering",
    "#GCP", "#machinelearning", "#python", "#opensource", "#tech",
    "#datascience", "#realtime", "#analytics", "",
]


def generate_event(seq_id: int) -> dict:
    """Return a single synthetic social-media event."""
    text = random.choice(SAMPLE_TEXTS) + " " + random.choice(HASHTAGS)
    return {
        "id": seq_id,
        "event_uuid": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "platform": random.choice(PLATFORMS),
        "text": text.strip(),
        "user_id": random.randint(1000, 99999),
        "likes": random.randint(0, 10000),
        "shares": random.randint(0, 5000),
        "region": random.choice(["us-east", "us-west", "eu-central", "ap-south"]),
        "lang": random.choice(["en", "es", "fr", "de", "pt"]),
    }


class DataGenerator:
    """Publishes synthetic events to Pub/Sub at the requested rate."""

    def __init__(self, project_id: str, topic_id: str, rate: int, duration: int,
                 batch_size: int = 100):
        self.project_id = project_id
        self.topic_id = topic_id
        self.rate = rate            # messages per second
        self.duration = duration    # seconds to run
        self.batch_size = batch_size

        self.publisher = pubsub_v1.PublisherClient(
            batch_settings=pubsub_v1.types.BatchSettings(
                max_messages=batch_size,
                max_bytes=10 * 1024 * 1024,   # 10 MB
                max_latency=0.05,              # 50 ms
            )
        )
        self.topic_path = self.publisher.topic_path(project_id, topic_id)

        self.total_published = 0
        self.total_errors = 0

    # ------------------------------------------------------------------
    def _publish_batch(self, events: list[dict]) -> int:
        """Publish a list of events; returns number successfully published."""
        futures = []
        for event in events:
            data = json.dumps(event).encode("utf-8")
            future = self.publisher.publish(
                self.topic_path,
                data,
                event_id=str(event["id"]),
                platform=event["platform"],
                publish_ts=event["timestamp"],
            )
            futures.append(future)

        ok = 0
        for future in futures:
            try:
                future.result(timeout=30)
                ok += 1
            except Exception as e:
                logger.warning("Publish error: %s", e)
                self.total_errors += 1
        return ok

    # ------------------------------------------------------------------
    def run(self):
        logger.info(
            "Starting generator: project=%s  topic=%s  rate=%d msg/s  duration=%ds",
            self.project_id, self.topic_id, self.rate, self.duration,
        )

        interval = 1.0 / self.rate          # ideal seconds between messages
        batch_interval = self.batch_size * interval   # seconds per batch

        seq_id = 0
        start_time = time.time()
        report_every = 10  # seconds

        next_report = start_time + report_every

        while True:
            elapsed = time.time() - start_time
            if elapsed >= self.duration:
                break

            batch_start = time.time()
            events = [generate_event(seq_id + i) for i in range(self.batch_size)]
            seq_id += self.batch_size

            published = self._publish_batch(events)
            self.total_published += published

            # Rate-limit: sleep for the remainder of the batch window
            elapsed_batch = time.time() - batch_start
            sleep_time = max(0, batch_interval - elapsed_batch)
            if sleep_time > 0:
                time.sleep(sleep_time)

            now = time.time()
            if now >= next_report:
                run_sec = now - start_time
                effective_rate = self.total_published / run_sec if run_sec > 0 else 0
                logger.info(
                    "Published %d events  |  %.1f msg/s  |  errors=%d  |  elapsed=%.1fs",
                    self.total_published, effective_rate, self.total_errors, run_sec,
                )
                next_report = now + report_every

        total_time = time.time() - start_time
        logger.info(
            "Generator finished. Total=%d  Errors=%d  Duration=%.1fs  Avg=%.1f msg/s",
            self.total_published, self.total_errors, total_time,
            self.total_published / total_time if total_time > 0 else 0,
        )


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Pub/Sub data generator for stream benchmark")
    parser.add_argument("--project",   required=True,       help="GCP project ID")
    parser.add_argument("--topic",     default="stream-benchmark-input", help="Pub/Sub topic name")
    parser.add_argument("--rate",      type=int, default=200, help="Messages per second (100-500)")
    parser.add_argument("--duration",  type=int, default=600, help="Duration in seconds (default 600 = 10 min)")
    parser.add_argument("--batch-size",type=int, default=100, help="Pub/Sub publish batch size")
    args = parser.parse_args()

    if not (50 <= args.rate <= 1000):
        parser.error("--rate should be between 50 and 1000")

    gen = DataGenerator(
        project_id=args.project,
        topic_id=args.topic,
        rate=args.rate,
        duration=args.duration,
        batch_size=args.batch_size,
    )
    gen.run()


if __name__ == "__main__":
    main()
