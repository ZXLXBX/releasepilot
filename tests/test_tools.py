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


class TestBlockerGuardrail(unittest.TestCase):
    """v2 fix 1: deterministic guardrail forces blocker for crash /
    data-loss / security issues, whatever the model passed."""

    def setUp(self):
        if rp.STATE_DB.exists():
            rp.STATE_DB.unlink()

    def tearDown(self):
        if rp.STATE_DB.exists():
            rp.STATE_DB.unlink()

    def _row(self, issue_id):
        with sqlite3.connect(rp.STATE_DB) as conn:
            return conn.execute(
                "SELECT category, priority, blocker, guardrail FROM triage "
                "WHERE issue_id=?",
                (issue_id,),
            ).fetchone()

    def test_rules_fire_on_the_right_sample_issues(self):
        by_id = {i["id"]: i for i in rp.list_issue_dicts()}
        self.assertIn("crash", rp.detect_blocker_reasons(by_id["ISSUE-101"]))
        self.assertIn("data-loss", rp.detect_blocker_reasons(by_id["ISSUE-104"]))
        for quiet in ("ISSUE-102", "ISSUE-103", "ISSUE-105"):
            self.assertEqual(rp.detect_blocker_reasons(by_id[quiet]), [], quiet)

    def test_security_keyword_fires(self):
        synthetic = {
            "title": "XSS vulnerability in the login form",
            "labels": "bug",
            "body": "An attacker can inject a script into the page.",
        }
        self.assertIn("security", rp.detect_blocker_reasons(synthetic))

    def test_data_loss_override_forces_blocker(self):
        out = rp.record_triage("ISSUE-104", "bug", "high", False)
        self.assertIn("GUARDRAIL OVERRIDE", out)
        row = self._row("ISSUE-104")
        self.assertEqual(row[2], 1)  # blocker forced on in the DB
        self.assertIn("data-loss", row[3])  # rule name persisted

    def test_crash_override_forces_blocker(self):
        out = rp.record_triage("ISSUE-101", "bug", "high", False)
        self.assertIn("GUARDRAIL OVERRIDE", out)
        self.assertEqual(self._row("ISSUE-101")[2], 1)

    def test_no_override_when_model_already_says_blocker(self):
        out = rp.record_triage("ISSUE-101", "bug", "high", True)
        self.assertNotIn("OVERRIDE", out)
        self.assertEqual(self._row("ISSUE-101")[2], 1)
        self.assertEqual(self._row("ISSUE-101")[3], "")  # nothing to override

    def test_no_override_for_ordinary_feature(self):
        out = rp.record_triage("ISSUE-102", "feature", "medium", False)
        self.assertNotIn("OVERRIDE", out)
        self.assertEqual(self._row("ISSUE-102")[2], 0)


class TestEnumValidation(unittest.TestCase):
    """v2 fix 2: category/priority are a strict vocabulary — the v1 drift
    value "enhancement" must be rejected, not recorded."""

    def setUp(self):
        if rp.STATE_DB.exists():
            rp.STATE_DB.unlink()

    def tearDown(self):
        if rp.STATE_DB.exists():
            rp.STATE_DB.unlink()

    def test_v1_drift_category_rejected(self):
        with self.assertRaises(ValueError):
            rp.record_triage("ISSUE-102", "enhancement", "medium", False)

    def test_invalid_priority_rejected(self):
        with self.assertRaises(ValueError):
            rp.record_triage("ISSUE-102", "feature", "urgent", False)

    def test_rejection_writes_nothing(self):
        try:
            rp.record_triage("ISSUE-102", "enhancement", "medium", False)
        except ValueError:
            pass
        if rp.STATE_DB.exists():
            with sqlite3.connect(rp.STATE_DB) as conn:
                count = conn.execute("SELECT COUNT(*) FROM triage").fetchone()[0]
            self.assertEqual(count, 0)

    def test_all_valid_values_accepted(self):
        for category in rp.CATEGORIES:
            out = rp.record_triage("ISSUE-102", category, "low", False)
            self.assertIn(f"category={category}", out)
        for priority in rp.PRIORITIES:
            out = rp.record_triage("ISSUE-103", "docs", priority, False)
            self.assertIn(f"priority={priority}", out)


class TestTriageSummary(unittest.TestCase):
    """v2 fix 3: follow-ups are answered from the database via the
    read-back tool."""

    def setUp(self):
        if rp.STATE_DB.exists():
            rp.STATE_DB.unlink()

    def tearDown(self):
        if rp.STATE_DB.exists():
            rp.STATE_DB.unlink()

    def test_empty_db_is_a_clean_answer(self):
        out = rp.get_triage_summary()
        self.assertIn("No triage has been recorded yet", out)

    def test_summary_lists_blockers_and_override(self):
        rp.record_triage("ISSUE-101", "bug", "high", True)
        rp.record_triage("ISSUE-104", "bug", "high", False)  # guardrail fires
        rp.record_triage("ISSUE-103", "docs", "low", False)
        out = rp.get_triage_summary()
        self.assertIn("3 triaged issue(s)", out)
        self.assertIn("Release blockers (2): ISSUE-101, ISSUE-104", out)
        self.assertIn("guardrail override", out)

    def test_summary_reflects_db_not_arguments(self):
        # Record, then re-record with a different call: the summary must
        # show the latest stored row (it reads the DB, not a transcript).
        rp.record_triage("ISSUE-105", "feature", "medium", False)
        rp.record_triage("ISSUE-105", "feature", "low", False)
        out = rp.get_triage_summary()
        self.assertIn("ISSUE-105 | category=feature | priority=low", out)


if __name__ == "__main__":
    unittest.main()
