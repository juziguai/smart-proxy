import json
import asyncio
import time
from tempfile import TemporaryDirectory
import unittest

from claude_usage_reader import ClaudeUsageReader
from stats_store import StatsStore
from usage_ingestion import ingest_usage_events


class UsageIngestionTests(unittest.TestCase):
    def test_ingestion_uses_batch_upsert(self):
        class FakeReader:
            def read_usage_events(self):
                return ["event-a", "event-b"]

        class FakeStore:
            def __init__(self):
                self.batches = []

            def upsert_usage_events(self, events):
                self.batches.append(events)

        store = FakeStore()

        count = ingest_usage_events(FakeReader(), store)

        self.assertEqual(count, 2)
        self.assertEqual(store.batches, [["event-a", "event-b"]])

    def test_ingests_usage_events_idempotently(self):
        with TemporaryDirectory() as temp_dir:
            store = StatsStore(f"{temp_dir}/stats.db")
            reader = ClaudeUsageReader(temp_dir)
            project_dir = reader.projects_dir / "project-a"
            project_dir.mkdir(parents=True)
            transcript = project_dir / "session-1.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "type": "assistant",
                        "timestamp": "2026-05-10T01:00:00+00:00",
                        "message": {
                            "model": "model-a",
                            "usage": {
                                "input_tokens": 11,
                                "output_tokens": 3,
                                "cache_read_input_tokens": 2,
                                "cache_creation_input_tokens": 1,
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            first_count = ingest_usage_events(reader, store)
            second_count = ingest_usage_events(reader, store)
            summary = store.get_summary("all")

        self.assertEqual(first_count, 1)
        self.assertEqual(second_count, 1)
        self.assertEqual(summary["usage"]["input_tokens"], 11)
        self.assertEqual(summary["usage"]["output_tokens"], 3)
        self.assertEqual(summary["usage"]["total_tokens"], 14)

    def test_ingestion_loop_does_not_block_event_loop(self):
        class SlowReader:
            def read_usage_events(self):
                time.sleep(0.3)
                return []

        async def run_check():
            with TemporaryDirectory() as temp_dir:
                store = StatsStore(f"{temp_dir}/stats.db")
                task = asyncio.create_task(
                    run_usage_ingestion_loop(
                        store,
                        interval_sec=60,
                        reader=SlowReader(),
                    )
                )
                try:
                    started = time.monotonic()
                    await asyncio.sleep(0.05)
                    elapsed = time.monotonic() - started
                    self.assertLess(elapsed, 0.2)
                finally:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

        from usage_ingestion import run_usage_ingestion_loop

        asyncio.run(run_check())


if __name__ == "__main__":
    unittest.main()
