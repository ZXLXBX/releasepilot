# ReleasePilot — demo transcript (text fallback for the demo video)

**Project:** ReleasePilot, a repo release assistant built with the
**OpenMinion 0.1** Python SDK (openminion 0.1.23) for **Cortensor
Hackathon #5** (DoraHacks).
**Video:** `releasepilot-demo.mp4` (~88 s, 1280×720, no narration —
on-screen text). Screenshots: `screenshot-1-title.png`,
`screenshot-2-triage-results.png`, `screenshot-3-release-notes.png`.

Everything below is from the **verified end-to-end run** — nothing is
reconstructed. Sources in the project repo: `docs/run-log.md` (run 4),
`logs/colab-verified/run-20261119T100600Z.json` (transcript + stats),
`logs/colab-verified/run6.log` (full remote session log),
`logs/colab-verified/doctor-20261119T100600Z.json` (diagnostics).

---

## 1. What it does

Point ReleasePilot at a small repo (the bundled sample project
"widgetkit") and it:

1. reads every open issue — `sample_repo/issues/*.md`, 5 real local files;
2. triages each one (category, priority, release-blocker flag);
3. records every decision in a local SQLite database (`state/triage.db`)
   via a write tool;
4. drafts the release notes and writes them to
   `output/RELEASE_NOTES.md`;
5. answers follow-up questions about its own triage in the same session.

Tools: `list_open_issues`, `read_release_context` (READ_ONLY) and
`record_triage`, `write_release_notes` (WRITE_SAFE) — the SDK's
permission scopes treat them differently, which matters in §3.

## 2. The one-command run

```bash
pip install -r requirements.txt        # openminion==0.1.23, pydantic
ollama pull llama3.1:8b                # the model `openminion setup` recommends
python run.py
```

Provider selection is config-only: the same `run.py` drives Ollama
(verified run), Cortensor Portal (when `CORTENSOR_API_KEY` is set), or
the offline `echo` provider as a plumbing smoke test.

**Verified run environment:** Google Colab Tesla T4, Ollama,
llama3.1:8b, 2026-11-19. (The local dev VM generated ~1.8 tokens/s with
a 1B model and timed out — see `docs/run-log.md`, runs 1–3.)

## 3. Key moment — approvals and tool calls (real excerpt)

Write tools pause under the action policy (`ENFORCE_SAFE_CONFIRM`).
`run.py` supplies an `approval_callback` that approves only
ReleasePilot's own two write tools and logs every decision. Excerpt
from `logs/colab-verified/run6.log`:

```text
$ cd /content/agent && python run.py
[releasepilot] provider=ollama model=llama3.1:8b
[approval] record_triage: approved        (×6 in total)
[approval] write_release_notes: approved  (×2 in total)
[turn 1] openminion: The next patch release for widgetkit is now
prepared. All open issues have been triaged, and their decisions have
been recorded. The release notes have been written and can be found in
the /content/agent/output/RELEASE_NOTES.md file.
```

Turn-1 statistics (from the run transcript JSON):

| Metric | Value |
|---|---|
| Wall time (turn 1) | 84 s (elapsed_ms 84323) |
| LLM calls | 17 |
| Tool calls | 9 |
| Approval decisions logged | 8 (6 × `record_triage`, 2 × `write_release_notes`) |
| Total tokens | 52,681 (51,244 in / 1,437 out) |
| Run state | completed |

## 4. Triage results — the actual SQLite contents

`state/triage.db`, 5 rows, dumped after the verified run
(`select * from triage order by issue_id`):

| Issue | Category | Priority | Blocker | Title |
|---|---|---|---|---|
| ISSUE-101 | bug | high | **1** | Crash on startup when config file is missing |
| ISSUE-102 | enhancement | medium | 0 | Add JSON export format alongside CSV |
| ISSUE-103 | docs | low | 0 | README quickstart uses a removed flag |
| ISSUE-104 | bug | high | 0 | Export silently truncates files over 10 MB |
| ISSUE-105 | enhancement | medium | 0 | Support custom color themes via a theme file |

All 5 priorities were correct, and the startup crash was correctly
flagged as the release blocker.

## 5. Generated release notes (verbatim, `output/RELEASE_NOTES.md`)

```markdown
### Must fix before release
- ISSUE-101: Crash on startup when config file is missing

### Bugs
- ISSUE-102: Add JSON export format alongside CSV
- ISSUE-104: Export silently truncates files over 10 MB

### Docs
- ISSUE-103: README quickstart uses a removed flag

### Future Features
- ISSUE-105: Support custom color themes via a theme file
```

## 6. What broke — honestly

1. **The data-loss issue was marked non-blocker.** ISSUE-104 (silent
   truncation of exports over 10 MB = silent data loss) was recorded
   with `blocker = false`, despite explicit instructions. Its priority
   (high) was right; the blocker call was wrong.
2. **Category drift.** The model wrote category `enhancement` where
   the instructions said `feature` — free-text tool arguments need a
   stricter (enum) schema. It also filed the JSON-export *feature*
   under "Bugs" in the notes.
3. **Turn 2 was refused by the freshness guardrail.** Asked "which
   issues did you mark as blockers in this session?", the runtime
   replied (verbatim, from run6.log):

   > [turn 2] openminion: I couldn't provide a grounded current
   > regulation answer because Missing live-data evidence for a
   > freshness-sensitive answer.. Please retry with live data
   > available so I can answer with sources and an exact date.

   A recall question about local session state is not
   freshness-sensitive. This and the echo-provider finding (offline
   runs "succeed" with zero tool calls) are drafted as issues for the
   OpenMinion repo in `docs/openminion-issue-draft.md`.

## 7. Closing facts

- Real artifacts on disk: `state/triage.db`, `output/RELEASE_NOTES.md`.
- Full evidence: transcripts + `openminion doctor --json` diagnostics
  under `logs/colab-verified/`.
- Unit tests: 6/6 pass (`python -m unittest discover tests`).
- SDK blocks exercised for real: agent execution, models, tools,
  permissions, sessions, durable state, diagnostics.
- Cortensor Portal/Gemma4 route is wired in `run.py` but was **not**
  run: both SDK Cortensor presets require a `CORTENSOR_API_KEY`, and
  the official hackathon docs say Portal access requires coordination
  with the hackathon team (optional for qualification).
