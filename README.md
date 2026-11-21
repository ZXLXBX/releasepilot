# ReleasePilot — a repo release assistant on the OpenMinion 0.1 SDK

**Cortensor Hackathon #5 (DoraHacks) — v2**

Demo: an 88-second walkthrough video of the v1 verified run was recorded
(sections: what it does, one-command run, verified-run stats, triage
results, generated release notes, what broke). A full text version with
verbatim run excerpts is in `docs/demo-transcript.md`. The v2 fixes are
code-level (below) and are evidenced by the v2 run logs, not a new video.

ReleasePilot prepares the next patch release of a small software project.
Point it at the sample repo in `sample_repo/` and it will:

1. read every open issue (`sample_repo/issues/*.md` — real files, real data),
2. triage each one (category, priority, release-blocker flag) and **record
   every decision in a local SQLite database**,
3. draft the release notes and **write them to `output/RELEASE_NOTES.md`**,
4. answer follow-up questions about its own triage in the same session.

It is a deliberately small, reliable, end-to-end workflow — the kind of
bounded local task OpenMinion 0.1 is built for — and it finishes with real
artifacts on disk you can inspect: a triage database and a release-notes
file, plus full run transcripts and diagnostics under `logs/`.

## What changed in v2

v1's verified run exposed three defects (see "What broke" #3 and #5).
v2 fixes exactly those three, in code, without changing the architecture:

1. **Deterministic blocker guardrail.** `record_triage` now checks the
   issue's own title/labels/body against an explicit rule set
   (`BLOCKER_RULES`: crash / data-loss / security patterns). If a rule
   fires, blocker is forced to `true` in code — the model's judgment no
   longer decides the catastrophic classes. The override is printed to
   the run log, returned to the model, persisted in the DB row, and
   appended to the release notes by `write_release_notes`. In both v2
   verification runs the model *again* passed `blocker=false` for the
   silent-data-loss issue, and the guardrail corrected it both times.
2. **Strict triage vocabulary.** `record_triage`'s category/priority are
   `typing.Literal` enums in the tool schema *and* validated in the tool
   body; an out-of-vocabulary value (v1 recorded `enhancement`) is
   rejected with an error instead of reaching the database. In the v2
   runs every recorded category is inside the enum. (The model did pick
   the in-enum but semantically wrong label `other` twice per run —
   the enum constrains vocabulary, not judgment; see "What broke" #5.)
3. **Follow-ups answered from the database.** A new read-only tool,
   `get_triage_summary`, reads the triage DB back, and the instructions
   route follow-up questions through it. In the v2 verified run, turn 2
   made exactly one tool call — `get_triage_summary` — and answered the
   blocker question from the stored rows, with no freshness-guardrail
   refusal.

## How to run

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt        # openminion==0.1.23, pydantic

# Option A — local model via Ollama (what our verified run used)
ollama serve &                         # separate terminal
ollama pull llama3.1:8b                # the model `openminion setup` recommends;
python run.py                          # 1B-class models did NOT complete our
                                       # workflow in testing (see docs/run-log.md)

# Option B — Cortensor Portal (hosted Cortensor inference)
export CORTENSOR_API_KEY=<your key>    # Portal/Gemma4 access is coordinated
python run.py                          # by the hackathon team; see below

# Option C — offline plumbing smoke test (no model; see "What broke")
RELEASEPILOT_PROVIDER=echo python run.py
```

One command (`python run.py`) does provider setup, both agent turns, and
diagnostics capture. Tests: `python -m unittest discover tests` (19 tests).

## OpenMinion building blocks tested

| Block | How ReleasePilot uses it |
|---|---|
| Agent execution | `openminion.Agent` with instructions + two sequential `run()` turns |
| Models | Provider-neutral: Ollama local model for the verified run; Cortensor Portal preset wired and ready (`cortensor-portal` / `oss-20b`) |
| Tools | Five `@tool`-decorated Python tools: `list_open_issues`, `read_release_context`, `record_triage`, `write_release_notes`, `get_triage_summary` (v2 read-back) |
| Permissions | Read tools declared `min_scope="READ_ONLY"`; write tools default to `WRITE_SAFE` — the action policy treats them differently, as designed |
| Sessions | Both turns share one `session_id`; turn 2 recalls turn 1's blockers |
| Memory / state | Triage decisions persist in SQLite (`state/triage.db`) via the write tool; OpenMinion session state persists under `.openminion/` |
| Structured output | Tool arguments are schema-typed (category/priority/blocker); see also `docs/` notes |
| Diagnostics | Every run saves `logs/run-<ts>.json` (text, run_id, session, metadata, stats) and `logs/doctor-<ts>.json` (`openminion doctor --json`) |

## What worked

- The SDK examples map 1:1 onto real code: `Agent + @tool + session_id`
  needed almost no adaptation. Decorated tools stay directly callable as
  plain Python functions, which made unit-testing the tool layer trivial.
- Provider switching really is config-only: the same `run.py` drives Ollama,
  Cortensor Portal, and echo with zero code changes.
- **Verified end-to-end run** (Colab Tesla T4, Ollama, llama3.1:8b — the
  model `openminion setup` recommends): turn 1 finished in 84 s with
  `tool_calls_count: 9`; all 5 issues were triaged into SQLite and the
  release notes were written to disk. Full evidence: `docs/run-log.md`
  and `logs/colab-verified/`.
- **v2 verified run** (same setup, 2026-11-21): turn 1 in 75 s, 17 LLM
  calls, `tool_calls_count: 9`, 54,658 tokens (v1: 84 s / 17 / 9 /
  52,681) — and turn 2 answered the blocker question from the database
  via one `get_triage_summary` call, no refusal. The blocker guardrail
  fired in both v2 runs. Evidence: `docs/run-log.md` (runs 5–6) and
  `logs/colab-verified-v2/`.
- The permissions system is real: write tools pause for confirmation
  (`ENFORCE_SAFE_CONFIRM`), and the documented `approval_callback`
  mechanism resolved it cleanly — 8 logged approval decisions in the
  verified run, all for our own two write tools.

## What broke / issues found

1. **Echo provider runs "succeed" without ever calling tools.** With the
   documented offline `echo` provider, a run — even with `forced_tools` —
   returns success echoing the prompt, with zero tool calls and no warning
   in the result metadata. Offline smoke tests can pass vacuously.
   Filed on the OpenMinion repo:
   https://github.com/openminion/openminion/issues/808
   (draft preserved in `docs/openminion-issue-draft.md`).
2. **Write tools pause the run, and a first-time builder gets no warning.**
   Without an approval callback (or an on-thread "yes"), a turn that
   reaches a WRITE_SAFE tool ends with the confirmation prompt as its
   result text. The behaviour is designed and documented in the
   delegation example, but the basic Agent/tool examples never mention
   it — our first live-model run ended there (run 3 in `docs/run-log.md`).
3. **Freshness guardrail misfires on session-recall questions.** Our
   turn 2 — "which issues did you mark as blockers in this session?" —
   was refused as a "freshness-sensitive regulation answer" needing live
   data. Filed on the OpenMinion repo:
   https://github.com/openminion/openminion/issues/809
   (draft preserved in `docs/openminion-issue-draft.md`).
   **v2 status:** worked around on our side — follow-ups now go through
   the `get_triage_summary` read-back tool and the turn-2 prompt is
   task-framed, so the answer is grounded in a tool result; in both v2
   runs turn 2 answered from the database with no refusal. The SDK
   behaviour itself (issue #809) is unchanged and still open.
4. **Cortensor Portal needs a `CORTENSOR_API_KEY` and coordinated access.**
   The hackathon brief we started from suggested testing Cortensor models
   needs no key/registration; in the shipped 0.1.23 SDK both
   `cortensor-portal` and `cortensor-router` presets require
   `CORTENSOR_API_KEY`, and the official hackathon docs say Portal/Gemma4
   access "requires coordination with the hackathon team" and is optional
   for qualification. We wired the Portal route and documented it; our
   verified run uses a local model instead. No penalty per the docs, but
   the brief could say this more plainly.
5. **Model size matters more than the examples suggest.** On a throttled
   shared-CPU VM, llama3.2:1b generated ~1.8 tokens/s and the provider
   timed out after ~11 min with nothing recorded; qwen2.5:0.5b was
   similar. llama3.1:8b on a T4 completed the whole workflow in 84 s.
   Even then, quality was imperfect: the data-loss issue was marked
   non-blocker against explicit instructions, the model used category
   `enhancement` instead of the instructed `feature`, and it filed a
   feature under "Bugs" in the notes. Small models + free-text tool
   arguments need stricter schemas (see improvements).
   **v2 status:** the two named defects are fixed in code — the
   data-loss blocker is now forced by the deterministic guardrail
   (it fired in both v2 runs, on the model's repeated `blocker=false`),
   and the category/priority vocabulary is enum-constrained (no
   out-of-vocabulary value reached the DB in either v2 run). What
   remains is *semantic* misjudgment inside the allowed vocabulary:
   the model labelled the data-loss and theme issues `other` instead
   of bug/feature in both v2 runs, set blocker=true for a docs issue
   once (run 6 — the guardrail forces blocker on for the catastrophic
   classes but deliberately doesn't veto other judgments), and its
   release-notes drafts were thinner than v1's grouped notes. An 8B
   model's judgment is still the weakest link; v2 just fences off the
   failures that must never ship.
6. **Environment gotcha:** sandboxes that export `no_proxy` with
   bracketed IPv6 entries (`[::1]`) break httpx for local provider URLs
   (`InvalidURL: Invalid port: ':1]'`). `run.py` normalises this at
   startup; the raw failure is preserved in `logs/ollama-run-stdout.txt`.

## What to improve (next)

- Add a delegation variant: a sub-agent that drafts notes while the main
  agent triages (the SDK's handoff/subagent examples are next on our list).
- Read real GitHub issues instead of local files (via an MCP/GitHub tool),
  keeping the same triage schema.
- Human-approval pause before `write_release_notes` overwrites an existing
  file, using the SDK's approval callback path.
- Try the same workflow on Cortensor Portal / Gemma4 once access is granted
  and compare tool-call reliability and latency against the local model.
- Structured-output (`output_type=`) final verdict object in addition to the
  free-text summary.
- Better semantic triage from the model itself: v2's guardrail and enums
  fence the catastrophic errors, but category *judgment* inside the enum
  (and notes drafting quality) still depends on the 8B model — a bigger
  model or a two-pass self-check is the next lever.

## Diagnostics

- Verified run transcript + doctor output: `logs/` (see `docs/run-log.md`
  for the summary and exact file names). v1: `logs/colab-verified/`;
  v2: `logs/colab-verified-v2/` (both runs, incl. triage DB dump).
- Reproduce our environment: `openminion --version` → 0.1.23,
  Python 3.12/3.13, Ollama + llama3.1:8b on a GPU runtime (Colab T4) for
  the verified run; local dev VM runs are documented in `docs/run-log.md`
  including their failures.

## License / notes

Sample repo data (`sample_repo/`) is fictional and written for this project.
No secrets or credentials are in this repo; provider keys come from the
environment only (see `.env.example`). AI-assisted development, as allowed
by the hackathon rules.
