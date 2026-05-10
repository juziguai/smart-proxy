import asyncio

from claude_usage_reader import ClaudeUsageReader


def ingest_usage_events(reader, stats_store):
    events = reader.read_usage_events()
    for event in events:
        stats_store.upsert_usage_event(event)
    return len(events)


async def run_usage_ingestion_loop(stats_store, interval_sec=5, reader=None, log=None):
    reader = reader or ClaudeUsageReader()
    while True:
        try:
            await asyncio.to_thread(ingest_usage_events, reader, stats_store)
        except Exception as exc:
            if log:
                log(f"usage ingestion failed: {exc}")
        await asyncio.sleep(interval_sec)
