"""ReleasePilot — a repo release assistant built on the OpenMinion 0.1 SDK.

Workflow (one real, end-to-end task on real local files):
  1. Read the open issues of a small sample repo (sample_repo/issues/*.md).
  2. Triage each issue: category + priority + release-blocker flag, recorded
     durably in a local SQLite database via a write tool.
  3. Draft the next patch release's notes from the triage and write them to
     output/RELEASE_NOTES.md via a write tool.
  4. Answer in the same session which issues block the release (session
     continuity is exercised by run.py's second turn).

OpenMinion building blocks exercised: agent execution, models (provider-
neutral config), custom decorated tools, permissions (READ_ONLY vs
WRITE_SAFE tool scopes), sessions, structured output (Pydantic), and
diagnostics (run metadata + `openminion doctor`, captured by run.py).
"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

from openminion import tool

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_REPO = Path(os.environ.get("RELEASEPILOT_REPO", PROJECT_ROOT / "sample_repo"))
OUTPUT_DIR = PROJECT_ROOT / "output"
STATE_DB = PROJECT_ROOT / "state" / "triage.db"


def _parse_issue(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    meta: dict[str, str] = {}
    body = text
    if text.startswith("---"):
        _, front, body = text.split("---", 2)
        for line in front.strip().splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                meta[key.strip()] = value.strip()
    return {
        "id": meta.get("id", path.stem),
        "title": meta.get("title", path.stem),
        "labels": meta.get("labels", ""),
        "reported": meta.get("reported", ""),
        "body": " ".join(body.split())[:400],
    }


def list_issue_dicts() -> list[dict]:
    """Plain-Python helper (unit-testable without the SDK runtime)."""
    return [_parse_issue(p) for p in sorted((SAMPLE_REPO / "issues").glob("*.md"))]


@tool(min_scope="READ_ONLY", idempotent=True)
def list_open_issues() -> str:
    """List every open issue in the sample repo with id, title, labels and body."""
    issues = list_issue_dicts()
    lines = [f"{len(issues)} open issues:"]
    for issue in issues:
        lines.append(
            f"- {issue['id']} | {issue['title']} | labels: {issue['labels']} "
            f"| reported: {issue['reported']} | {issue['body']}"
        )
    return "\n".join(lines)


@tool(min_scope="READ_ONLY", idempotent=True)
def read_release_context() -> str:
    """Return the repo's current version and the top of its CHANGELOG."""
    version = (SAMPLE_REPO / "VERSION").read_text(encoding="utf-8").strip()
    changelog = (SAMPLE_REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    return f"Current version: {version}\n\nCHANGELOG (first 1200 chars):\n{changelog[:1200]}"


@tool(idempotent=True)
def record_triage(issue_id: str, category: str, priority: str, blocker: bool) -> str:
    """Record one triage decision in the local SQLite triage database.

    category: one of bug, feature, docs, other.
    priority: one of high, medium, low.
    blocker: true if this issue must be fixed before the next release.
    """
    STATE_DB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(STATE_DB) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS triage ("
            "issue_id TEXT PRIMARY KEY, category TEXT, priority TEXT, blocker INTEGER)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO triage VALUES (?, ?, ?, ?)",
            (issue_id, category, priority, 1 if blocker else 0),
        )
    return f"recorded triage for {issue_id}: category={category} priority={priority} blocker={blocker}"


@tool(idempotent=True)
def write_release_notes(content: str) -> str:
    """Write the draft release notes markdown to output/RELEASE_NOTES.md."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUTPUT_DIR / "RELEASE_NOTES.md"
    target.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} chars to {target}"


TOOLS = [list_open_issues, read_release_context, record_triage, write_release_notes]

INSTRUCTIONS = """You are ReleasePilot, a release assistant for the widgetkit repo.
Work only from tool results — never invent issues.

Step 1: call list_open_issues and read_release_context.
Step 2: for EVERY issue, call record_triage exactly once:
  - category from its labels (bug / enhancement->feature / docs / other),
  - priority high for crashes and silent data loss, medium for ordinary
    bugs and small enhancements, low for docs and large future features,
  - blocker=true only for the crash and the silent data-loss bug.
Step 3: call write_release_notes with markdown release notes for the next
  patch release (1.4.1) that list the blockers as "must fix before release",
  group the remaining issues by category, and defer the large theme feature
  to a future minor release.
Step 4: finish with a one-paragraph summary naming the blocker issue ids.
"""

__all__ = ["TOOLS", "INSTRUCTIONS", "list_issue_dicts", "SAMPLE_REPO", "STATE_DB"]
