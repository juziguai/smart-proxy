import json
from tempfile import TemporaryDirectory
import unittest

from claude_usage_reader import ClaudeUsageReader


class ClaudeUsageReaderTests(unittest.TestCase):
    def test_reads_usage_from_main_and_subagent_transcripts(self):
        with TemporaryDirectory() as temp_dir:
            reader = ClaudeUsageReader(temp_dir)
            project_dir = reader.projects_dir / "project-a"
            subagent_dir = project_dir / "session-1" / "subagents"
            subagent_dir.mkdir(parents=True)

            self.write_jsonl(
                project_dir / "session-1.jsonl",
                [
                    {
                        "type": "assistant",
                        "timestamp": "2026-05-10T01:00:00+00:00",
                        "sessionId": "session-1",
                        "message": {
                            "model": "model-a",
                            "usage": {
                                "input_tokens": 10,
                                "output_tokens": 5,
                                "cache_read_input_tokens": 3,
                                "cache_creation_input_tokens": 2,
                                "server_tool_use": {
                                    "web_search_requests": 1,
                                    "web_fetch_requests": 0,
                                },
                                "service_tier": "standard",
                                "speed": "fast",
                            },
                        },
                    }
                ],
            )
            self.write_jsonl(
                subagent_dir / "agent-abc.jsonl",
                [
                    {
                        "type": "assistant",
                        "timestamp": "2026-05-10T02:00:00+00:00",
                        "session_id": "session-1",
                        "message": {
                            "model": "model-b",
                            "usage": {
                                "input_tokens": 7,
                                "output_tokens": 4,
                            },
                        },
                    }
                ],
            )

            events = reader.read_usage_events()

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].model, "model-a")
        self.assertEqual(events[0].input_tokens, 10)
        self.assertEqual(events[0].output_tokens, 5)
        self.assertEqual(events[0].cache_read_input_tokens, 3)
        self.assertEqual(events[0].cache_creation_input_tokens, 2)
        self.assertEqual(events[0].web_search_requests, 1)
        self.assertEqual(events[0].web_fetch_requests, 0)
        self.assertEqual(events[0].service_tier, "standard")
        self.assertEqual(events[0].speed, "fast")
        self.assertTrue(events[0].source_file.endswith("session-1.jsonl"))
        self.assertEqual(events[0].source_line, 1)

        self.assertEqual(events[1].model, "model-b")
        self.assertEqual(events[1].input_tokens, 7)
        self.assertEqual(events[1].output_tokens, 4)
        self.assertEqual(events[1].cache_read_input_tokens, 0)
        self.assertEqual(events[1].cache_creation_input_tokens, 0)
        self.assertEqual(events[1].web_search_requests, 0)
        self.assertEqual(events[1].web_fetch_requests, 0)
        self.assertTrue(events[1].source_file.endswith("agent-abc.jsonl"))

    def test_skips_malformed_json_and_entries_without_usage(self):
        with TemporaryDirectory() as temp_dir:
            reader = ClaudeUsageReader(temp_dir)
            project_dir = reader.projects_dir / "project-a"
            project_dir.mkdir(parents=True)
            transcript = project_dir / "session-1.jsonl"
            transcript.write_text(
                "\n".join(
                    [
                        "{bad json",
                        json.dumps(
                            {
                                "type": "assistant",
                                "timestamp": "2026-05-10T01:00:00+00:00",
                                "message": {"model": "model-a"},
                            }
                        ),
                        json.dumps(
                            {
                                "type": "user",
                                "timestamp": "2026-05-10T01:01:00+00:00",
                                "message": {
                                    "usage": {
                                        "input_tokens": 99,
                                        "output_tokens": 99,
                                    }
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "assistant",
                                "timestamp": "2026-05-10T01:02:00+00:00",
                                "message": {
                                    "model": "model-a",
                                    "usage": {
                                        "input_tokens": 1,
                                        "output_tokens": 2,
                                    },
                                },
                            }
                        ),
                    ]
                ),
                encoding="utf-8",
            )

            events = reader.read_usage_events()

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].input_tokens, 1)
        self.assertEqual(events[0].output_tokens, 2)
        self.assertEqual(events[0].source_line, 4)

    def write_jsonl(self, path, entries):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(json.dumps(entry) for entry in entries),
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
