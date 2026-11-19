"""Unit/smoke tests for ReleasePilot's tools and issue parsing.

Run:  python -m pytest tests/ -q   (or: python -m unittest discover tests)
These tests exercise the plain tool logic directly — no model, no network.
"""

import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import releasepilot as rp


class TestIssueParsing(unittest.TestCase):
    def test_five_sample_issues_parse(self):
        issues = rp.list_issue_dicts()
        self.assertEqual(len(issues), 5)
        ids = [i["id"] for i in issues]
        self.assertEqual(ids, ["ISSUE-101", "ISSUE-102", "ISSUE-103", "ISSUE-104", "ISSUE-105"])

    def test_frontmatter_fields(self):
        first = rp.list_issue_dicts()[0]
        self.assertIn("Crash on startup", first["title"])
        self.assertIn("bug", first["labels"])
        self.assertTrue(first["body"])


class TestTools(unittest.TestCase):
    def test_list_open_issues_tool_text(self):
        # Decorated tools remain callable as plain functions in the SDK.
        out = rp.list_open_issues()
        self.assertIn("5 open issues", out)
        self.assertIn("ISSUE-104", out)

    def test_read_release_context(self):
        out = rp.read_release_context()
        self.assertIn("1.4.0", out)
        self.assertIn("Changelog", out)

    def test_record_triage_writes_sqlite(self):
        if rp.STATE_DB.exists():
            rp.STATE_DB.unlink()
        rp.record_triage("ISSUE-999", "bug", "high", True)
        with sqlite3.connect(rp.STATE_DB) as conn:
            row = conn.execute(
                "SELECT category, priority, blocker FROM triage WHERE issue_id='ISSUE-999'"
            ).fetchone()
        self.assertEqual(row, ("bug", "high", 1))
        rp.STATE_DB.unlink()

    def test_write_release_notes(self):
        # Write to a temp dir so the test never clobbers a real run artifact.
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            original = rp.OUTPUT_DIR
            rp.OUTPUT_DIR = Path(tmp)
            try:
                rp.write_release_notes("# Release notes test\n")
                target = Path(tmp) / "RELEASE_NOTES.md"
                self.assertTrue(target.exists())
                self.assertIn("Release notes test", target.read_text())
            finally:
                rp.OUTPUT_DIR = original


if __name__ == "__main__":
    unittest.main()
