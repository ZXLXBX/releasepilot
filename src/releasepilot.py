"""ReleasePilot — a repo release assistant built on the OpenMinion 0.1 SDK.

Workflow (one real, end-to-end task on real local files):
  1. Read the open issues of a small sample repo (sample_repo/issues/*.md).
  2. Triage each issue: category + priority + release-blocker flag, recorded
     durably in a local SQLite database via a write tool.
  3. Draft the next patch release's notes from the triage and write them to
     output/RELEASE_NOTES.md via a write tool.
  4. Answer follow-up questions about the triage from the database itself
     (via the read-back tool `get_triage_summary`), in the same session.

v2 changes (surgical fixes for the three defects the v1 verified run
exposed — see README "What broke" and docs/run-log.md):
  * Deterministic blocker guardrail in `record_triage`: if an issue's
    title/labels/body match the crash / data-loss / security rules in
    BLOCKER_RULES, blocker is forced to true in code — the model cannot
    talk its way out of it (v1 marked the silent-data-loss issue
    non-blocker against instructions).
  * Strict enums: `record_triage`'s category/priority are typing.Literal
    (so the tool schema carries an enum) AND validated in the tool body,
    so an out-of-vocabulary value (v1's `enhancement`) is rejected with
    an error the model must retry against.
  * Read-back tool `get_triage_summary` reads the triage DB, and the
    instructions tell the agent to answer follow-ups from it (v1's
    session-recall turn was refused by the SDK freshness guardrail).

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
from typing import Literal

from openminion import tool

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_REPO = Path(os.environ.get("RELEASEPILOT_REPO", PROJECT_ROOT / "sample_repo"))
OUTPUT_DIR = PROJECT_ROOT / "output"
STATE_DB = PROJECT_ROOT / "state" / "triage.db"

# --- Strict vocabularies for the triage schema (v2 fix for category drift) ---
Category = Literal["bug", "feature", "docs", "other"]
Priority = Literal["high", "medium", "low"]
CATEGORIES: tuple[str, ...] = ("bug", "feature", "docs", "other")
PRIORITIES: tuple[str, ...] = ("high", "medium", "low")

# --- Deterministic release-blocker guardrail (v2) ---
# Small, explicit rule set. Each rule is (name, pattern) matched against an
# issue's title + labels + body. If ANY rule fires, the issue is a release
# blocker by construction: record_triage forces blocker=true no matter what
# the model passed. Deliberately narrow — these are the three impact classes
# where a wrong "not a blocker" call ships a broken release.
BLOCKER_RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "crash",
        re.compile(r"\bcrash\w*\b|\bsegfault\b|\bkernel panic\b", re.IGNORECASE),
    ),
    (
        "data-loss",
        re.compile(
            r"data[-\s]loss|data[-\s]corruption|silent data|"
            r"silently\s+(truncat|corrupt|delet|dropp|overwrit)\w*",
            re.IGNORECASE,
        ),
    ),
    (
        "security",
        re.compile(
            r"\bsecurity\b|vulnerab\w*|\bexploit\w*\b|\bxss\b|sql injection|"
            r"authentication bypass|credential\w*\s+(leak|expos)\w*",
            re.IGNORECASE,
        ),
    ),
]


def detect_blocker_reasons(issue: dict) -> list[str]:
    """Return the names of BLOCKER_RULES that fire for an issue dict.

    Pure function of the issue's title, labels and body — unit-testable
    without the SDK runtime. Empty list means no rule fired.
    """
    haystack = " ".join(
        [issue.get("title", ""), issue.get("labels", ""), issue.get("body", "")]
    )
    return [name for name, pattern in BLOCKER_RULES if pattern.search(haystack)]


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


def _issue_by_id(issue_id: str) -> dict | None:
    for issue in list_issue_dicts():
        if issue["id"] == issue_id:
            return issue
    return None


def _connect() -> sqlite3.Connection:
    STATE_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(STATE_DB)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS triage ("
        "issue_id TEXT PRIMARY KEY, category TEXT, priority TEXT, blocker INTEGER)"
    )
    # v2 adds a `guardrail` column (rule names behind an override, '' when
    # none). Migrate a v1-shaped table in place instead of failing on it.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(triage)")}
    if "guardrail" not in columns:
        conn.execute("ALTER TABLE triage ADD COLUMN guardrail TEXT NOT NULL DEFAULT ''")
    return conn


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
def record_triage(issue_id: str, category: Category, priority: Priority, blocker: bool) -> str:
    """Record one triage decision in the local SQLite triage database.

    category: exactly one of bug, feature, docs, other (no other value is
    accepted — e.g. use "feature", not "enhancement").
    priority: exactly one of high, medium, low.
    blocker: true if this issue must be fixed before the next release.
    Note: a deterministic guardrail in this tool forces blocker=true when
    the issue text indicates a crash, data loss, or a security problem,
    regardless of the blocker value passed.
    """
    # Enum validation lives in the body as well as the Literal schema:
    # decorated tools stay directly callable, and a model that ignores the
    # schema gets a hard error it must retry against — never a silent
    # free-text value in the database (v1 recorded category "enhancement").
    if category not in CATEGORIES:
        raise ValueError(
            f"invalid category {category!r}: must be one of {', '.join(CATEGORIES)}"
        )
    if priority not in PRIORITIES:
        raise ValueError(
            f"invalid priority {priority!r}: must be one of {', '.join(PRIORITIES)}"
        )

    # Deterministic blocker guardrail: rules over the issue's own text
    # outrank the model's blocker judgment for crash / data-loss /
    # security impact. The override is returned to the model, printed to
    # the run log, and persisted in the row's `guardrail` column.
    guardrail_note = ""
    guardrail_rules = ""
    issue = _issue_by_id(issue_id)
    if issue is not None:
        reasons = detect_blocker_reasons(issue)
        if reasons and not blocker:
            blocker = True
            guardrail_rules = ",".join(reasons)
            guardrail_note = (
                f"GUARDRAIL OVERRIDE: {issue_id} matches blocker rule(s) "
                f"[{guardrail_rules}] — blocker forced to true "
                f"(the value passed was false)"
            )
            print(f"[guardrail] {guardrail_note}")

    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO triage VALUES (?, ?, ?, ?, ?)",
            (issue_id, category, priority, 1 if blocker else 0, guardrail_rules),
        )
    result = (
        f"recorded triage for {issue_id}: category={category} "
        f"priority={priority} blocker={blocker}"
    )
    if guardrail_note:
        result += f" [{guardrail_note}]"
    return result


@tool(min_scope="READ_ONLY", idempotent=True)
def get_triage_summary() -> str:
    """Read the recorded triage decisions back from the SQLite database.

    This is the source of truth for follow-up questions about the triage
    (which issues block the release, counts, categories, guardrail
    overrides) — answer those from this tool's result, not from memory.
    """
    if not STATE_DB.exists():
        return "No triage has been recorded yet (the triage database does not exist)."
    with _connect() as conn:
        rows = conn.execute(
            "SELECT issue_id, category, priority, blocker, guardrail "
            "FROM triage ORDER BY issue_id"
        ).fetchall()
    if not rows:
        return "No triage has been recorded yet (the triage database is empty)."
    blockers = [row for row in rows if row[3]]
    lines = [
        f"{len(rows)} triaged issue(s) recorded in the triage database.",
        "Release blockers ("
        + str(len(blockers))
        + "): "
        + (", ".join(row[0] for row in blockers) if blockers else "none"),
        "All recorded triage:",
    ]
    for issue_id, category, priority, blocker, guardrail in rows:
        line = (
            f"- {issue_id} | category={category} | priority={priority} "
            f"| blocker={'yes' if blocker else 'no'}"
        )
        if guardrail:
            line += f" | guardrail override: rule(s) [{guardrail}] forced blocker"
        lines.append(line)
    return "\n".join(lines)


def _guardrail_overrides() -> list[tuple[str, str]]:
    """(issue_id, rules) pairs for rows where the guardrail forced blocker."""
    if not STATE_DB.exists():
        return []
    with _connect() as conn:
        return conn.execute(
            "SELECT issue_id, guardrail FROM triage "
            "WHERE guardrail != '' ORDER BY issue_id"
        ).fetchall()


@tool(idempotent=True)
def write_release_notes(content: str) -> str:
    """Write the draft release notes markdown to output/RELEASE_NOTES.md.

    If the deterministic guardrail overrode any triage decision, a
    "Guardrail overrides" section listing them is appended in code (when
    the provided content doesn't already have one), so the override is
    visible in the shipped notes even if the draft omitted it.
    """
    overrides = _guardrail_overrides()
    if overrides and "guardrail" not in content.lower():
        section = ["", "## Guardrail overrides (deterministic)", ""]
        section.append(
            "ReleasePilot's code-level guardrail forced release_blocker=true "
            "for the following issue(s), based on their text:"
        )
        for issue_id, rules in overrides:
            section.append(f"- {issue_id} — matched rule(s): {rules}")
        content = content.rstrip() + "\n" + "\n".join(section) + "\n"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUTPUT_DIR / "RELEASE_NOTES.md"
    target.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} chars to {target}"


TOOLS = [
    list_open_issues,
    read_release_context,
    record_triage,
    write_release_notes,
    get_triage_summary,
]

INSTRUCTIONS = """You are ReleasePilot, a release assistant for the widgetkit repo.
Work only from tool results — never invent issues.

Step 1: call list_open_issues and read_release_context.
Step 2: for EVERY issue, call record_triage exactly once:
  - category MUST be exactly one of: bug, feature, docs, other
    (map the "enhancement" label to feature). The tool rejects any other
    value with an error — if that happens, retry with a valid category.
  - priority high for crashes and silent data loss, medium for ordinary
    bugs and small enhancements, low for docs and large future features,
  - blocker=true only for the crash and the silent data-loss bug.
  Note: record_triage has a deterministic guardrail — if an issue's text
  indicates a crash, data loss, or a security problem, the tool forces
  blocker=true itself and says "GUARDRAIL OVERRIDE" in its result. If you
  see that, name the overridden issue in your final summary.
Step 3: call write_release_notes with markdown release notes for the next
  patch release (1.4.1) that list the blockers as "must fix before release",
  group the remaining issues by category, and defer the large theme feature
  to a future minor release.
Step 4: finish with a one-paragraph summary naming the blocker issue ids.
Follow-up questions: whenever you are asked about the triage afterwards
(which issues block the release, counts, categories), call
get_triage_summary and answer from the database — it is the source of
truth, not your memory of the session.
"""

__all__ = [
    "TOOLS",
    "INSTRUCTIONS",
    "list_issue_dicts",
    "detect_blocker_reasons",
    "BLOCKER_RULES",
    "CATEGORIES",
    "PRIORITIES",
    "SAMPLE_REPO",
    "STATE_DB",
]
