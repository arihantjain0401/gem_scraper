import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "export_claude_sessions.py"
SPEC = importlib.util.spec_from_file_location("exporter", MODULE_PATH)
exporter = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(exporter)


class ExporterTests(unittest.TestCase):
    def test_summary_redacts_secret_and_extracts_file(self):
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "abc.jsonl"
            events = [
                {"type": "user", "sessionId": "abc", "timestamp": "2026-09-21T10:00:00Z", "cwd": "/work/gem", "message": {"content": "Fix it token=super-secret-value"}},
                {"type": "assistant", "sessionId": "abc", "timestamp": "2026-09-21T10:05:00Z", "message": {"content": [
                    {"type": "tool_use", "name": "Edit", "input": {"file_path": "/work/gem/app.py"}},
                    {"type": "text", "text": "Fixed the parser."},
                ]}},
            ]
            transcript.write_text("".join(json.dumps(e) + "\n" for e in events))
            _, summary = exporter.render_summary(transcript)
            self.assertIn("[REDACTED]", summary)
            self.assertNotIn("super-secret-value", summary)
            self.assertIn("/work/gem/app.py", summary)
            self.assertIn("Fixed the parser.", summary)


if __name__ == "__main__":
    unittest.main()
