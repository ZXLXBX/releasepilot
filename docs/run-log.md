# Run log — every run we actually made, in order (2026-11-19 v1, 2026-11-21 v2)

Nothing here is fabricated: each entry names its transcript file in `logs/`.
Local runs were on this project's dev VM (1.5 shared vCPU, 6 GB RAM);
the verified run was on a Google Colab Tesla T4 runtime (the project's
standing route for heavy compute), same code, same SDK version (0.1.23).

## Run 1 — echo provider, local (plumbing smoke test)
- File: `logs/run-20261119T091608Z.json`, doctor: `logs/doctor-20261119T091608Z.json`
- Result: "success", but both turns simply echo the prompt back
  (`openminion: <prompt>`). Zero tool calls — the echo adapter always
  returns `tool_calls=[]`, even with `forced_tools`. Config, session,
  runtime and doctor plumbing all work; tool behaviour cannot be
  validated this way. → Issue draft #1 (`docs/openminion-issue-draft.md`).

## Run 2 — Ollama llama3.2:1b, local VM
- Files: `logs/run-20261119T093057Z.json`, stdout in `logs/ollama-run-stdout.txt`
- First attempt failed instantly: `InvalidURL: Invalid port: ':1]'` —
  traced to this sandbox's `no_proxy` containing bracketed IPv6 entries
  (`[::1]`), which httpx misparses for local URLs. `run.py` now normalises
  `no_proxy`/`NO_PROXY` at startup; after the fix the provider connected.
- Second attempt: provider TIMEOUT after ~11 minutes. Ollama's own log
  showed generation at **1.81 tokens/s** (Q8 1.2B model on throttled
  shared CPU, ~2,400-token system prompt reprocessed per step). No triage
  rows were recorded. A qwen2.5:0.5b attempt was only marginally faster
  (~3.1 t/s) and was abandoned for the same reason.
- Conclusion: this VM cannot serve a local model for a multi-step agent;
  compute moved to Colab (T4 GPU).

## Run 3 — Ollama llama3.2:1b, Colab T4 (first Colab run)
- File: `logs/run-20261119T095457Z.json` (captured on Colab; summary here)
- Turn 1 (102 s) ended at a **policy confirmation pause** for
  `write_release_notes` (`ENFORCE_SAFE_CONFIRM`): with no approval
  callback and no on-thread reply, the turn's result text *is* the
  confirmation prompt and `tool_calls_count` is 0. This is the SDK's
  designed behaviour — the delegation example documents the two ways to
  answer (on-thread `run("yes", thread_id=…)` or `approval_callback`) —
  but nothing in the basic `Agent` examples warns a first-time builder
  that a write tool will pause the run like this.
- Fix in `run.py`: an `approval_callback` that approves only
  ReleasePilot's two write tools and logs every decision, plus an
  on-thread fallback loop.

## Run 4 — Ollama llama3.1:8b, Colab T4 — ✅ VERIFIED END-TO-END RUN
- Files: `logs/colab-verified/run-20261119T100600Z.json`,
  `logs/colab-verified/doctor-20261119T100600Z.json`,
  `logs/colab-verified/run6.log` (full remote session log)
- llama3.1:8b is the model `openminion setup` recommends for Ollama.
- Turn 1: **84 s, 17 LLM calls, `tool_calls_count: 9`**. The approval
  callback logged 8 decisions (6× `record_triage`, 2× `write_release_notes`
  — the model re-did one triage and one notes draft before finalising).
  Agent's own summary: *"The next patch release for widgetkit is now
  prepared… release notes… in output/RELEASE_NOTES.md."*
- Real artifacts produced by the agent (downloaded from the run):
  - `state/triage.db` — 5 rows:
    ISSUE-101 bug/high/blocker ✓ · ISSUE-102 enhancement/medium ·
    ISSUE-103 docs/low ✓ · ISSUE-104 bug/high · ISSUE-105 enhancement/medium
  - `output/RELEASE_NOTES.md` — grouped notes (Must fix / Bugs / Docs /
    Future Features), reproduced in this repo's `output/`.
- Quality, honestly: priorities were all right; the crash blocker was
  right; **the silent-data-loss issue (ISSUE-104) was marked
  blocker=false despite explicit instructions**, and the model used the
  category word `enhancement` instead of the instructed `feature`
  (schema drift — our tool accepts free text; a stricter enum is future
  work). The notes also filed the JSON-export *feature* under "Bugs".
- Turn 2 (session recall): **failed in an interesting way** — the
  runtime's freshness guardrail intercepted a question about the
  session's own triage and replied *"I couldn't provide a grounded
  current regulation answer because Missing live-data evidence for a
  freshness-sensitive answer…"*. A recall question about local session
  state is not freshness-sensitive. → Issue draft #2.

## Run 5 — v2 first run, Ollama llama3.1:8b, Colab T4 (2026-11-21)
- Files: `logs/colab-verified-v2/run-20261121T015738Z.json`,
  `logs/colab-verified-v2/doctor-20261121T015738Z.json`,
  `logs/colab-verified-v2/session-run.log`,
  `logs/colab-verified-v2/RELEASE_NOTES-run1.md`,
  triage rows in `logs/colab-verified-v2/triage-db-dump.txt`
- v2 fixes under test: deterministic blocker guardrail, enum-constrained
  triage schema, `get_triage_summary` read-back tool for follow-ups.
- Turn 1: 182 s, 16 LLM calls, `tool_calls_count: 9`, 50,690 tokens,
  7 approval decisions, 1 tool error (the model's first
  `write_release_notes` call passed no `content` argument and was blocked
  by the SDK's pydantic argument validation; the retry succeeded).
- **The guardrail fired on the exact v1 defect**: the model passed
  `blocker=false` for ISSUE-104 again, and the tool overrode it —
  `[guardrail] GUARDRAIL OVERRIDE: ISSUE-104 matches blocker rule(s)
  [data-loss] — blocker forced to true`. Final DB: 101 bug/high/blocker ·
  102 feature/medium · 103 docs/low · 104 other/low/**blocker**
  (guardrail: data-loss) · 105 other/medium. No out-of-vocabulary
  category was recorded (`enhancement` is gone), but the model twice
  picked the in-enum-but-wrong label `other` (ISSUE-104, ISSUE-105).
- `write_release_notes` appended the deterministic "Guardrail overrides"
  section in code, as designed. The model's own notes draft, however, was
  a bare triage list, not the grouped notes v1 produced.
- Turn 2: no freshness-guardrail refusal; the agent called tools
  (2 tool calls) including `get_triage_summary`, which returned the
  correct DB state ("Release blockers (2): ISSUE-101, ISSUE-104"). The
  final composed answer, though, only surfaced the version half of the
  question (and named the current version, 1.4.0).

## Run 6 — v2 second run, Ollama llama3.1:8b, Colab T4 — ✅ V2 VERIFIED RUN (2026-11-21)
- Files: `logs/colab-verified-v2/run-20261121T020321Z.json`,
  `logs/colab-verified-v2/doctor-20261121T020321Z.json`,
  `logs/colab-verified-v2/session-run2.log`,
  `logs/colab-verified-v2/RELEASE_NOTES-run2.md` (also in `output/`),
  triage rows in `logs/colab-verified-v2/triage-db-dump.txt`
- Fresh state (DB and output deleted before the run; same code as run 5).
- Turn 1: **75 s, 17 LLM calls, `tool_calls_count: 9`, 54,658 tokens**,
  7 approval decisions, 1 tool error (same empty-argument first
  `write_release_notes` call, blocked by argument validation, retried).
  Profile is directly comparable to v1's verified run (84 s / 17 / 9 /
  52,681).
- **Guardrail fired again**: model passed `blocker=false` for ISSUE-104;
  override forced blocker=true (rule: data-loss), printed to the run log,
  persisted in the row, and appended to the release notes in code.
  Final DB: 101 bug/high/blocker · 102 feature/medium · 103 docs/low/
  **blocker** · 104 other/medium/**blocker** (guardrail: data-loss) ·
  105 other/low.
- Two honest blemishes: the model set blocker=true for the docs issue
  ISSUE-103 on its own (the guardrail only forces blocker *on* for
  crash/data-loss/security text; it deliberately does not veto other
  model judgments), and it again labelled ISSUE-104/105 `other` instead
  of bug/feature — inside the enum, semantically wrong. Its notes draft
  was again a one-line placeholder rather than grouped notes; the file
  on disk is that draft plus the code-appended guardrail section.
- Turn 2: 25 s, 5 LLM calls, **1 tool call — `get_triage_summary`** —
  and the answer came straight from the database: *"The release blockers
  are ISSUE-101, ISSUE-103, and ISSUE-104."* (faithful to the DB,
  including the model's own ISSUE-103 call). No freshness refusal.
- Unit tests on the same Colab runtime (Python 3.13, SDK 0.1.23):
  **19/19 pass** (6 v1 tests + 13 new: guardrail rules/overrides,
  enum rejection, read-back summary).

## Probe — SDK tool_agent pattern in isolation (llama3.1:8b, Colab)
- In `logs/colab-verified/run6.log`: a minimal `Agent(tools=[lookup_order],
  forced_tools=["lookup_order"])` run answered conversationally and
  reported `tool_calls_count: 0`, while ReleasePilot's task-style prompt
  in the same environment dispatched 9 real tool calls. Small,
  single-question prompts seem to be answered at the brain's entry step
  before the tool loop engages; task-framed prompts reach the tool loop.
  Worth knowing when writing SDK examples/tests.
