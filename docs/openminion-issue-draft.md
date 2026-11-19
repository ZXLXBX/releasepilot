# DRAFT — GitHub Issue to file on openminion/openminion (not yet filed)

> Filing this issue is a later, human-approved step. This draft is saved so
> the submission README can link it once filed.

**Title:** Echo (demo) provider silently produces "successful" runs that never call tools — SDK examples give no signal this will happen

**Affected version:** openminion 0.1.23 (source checkout, Python 3.12, Linux)

**Steps to reproduce**
1. `openminion config init --provider echo`
2. Run `examples/sdk/tool_agent.py` (or any `Agent(tools=[...], forced_tools=[...])`
   program) against the echo-configured default agent.
3. Observe the run completes successfully and prints the prompt text back.

**Expected behavior**
Either the echo provider should honour `forced_tools` with a synthetic tool
call (so tool plumbing can be smoke-tested offline), or `Agent.run()` /
`openminion doctor` should surface a clear warning that the configured
provider cannot call tools and the run exercised no tool code paths.

**Actual behavior**
The run returns `run_state` success with the user prompt echoed as the reply
(`openminion: <prompt>`). No tool is invoked — including explicitly forced
tools — and nothing in the result metadata flags that zero tool calls
happened. A builder following the SDK examples can believe their tool agent
works when it has never executed a tool.

**Impact**
Hackathon/CI smoke tests built on the documented offline provider can pass
vacuously. This cost us one debugging cycle: our release-assistant's tools
were only proven once we switched to a real local model (Ollama).

**Evidence**
- Run transcript: `logs/run-20261119T091608Z.json` in this project (echo run,
  both turns echo the prompt, no tool calls in metadata).
- Echo adapter source: `src/openminion/modules/llm/providers/echo/adapter.py`
  always returns `tool_calls=[]`.

**Suggested fix**
In the runtime, when a run ends with tools registered (or forced) but zero
tool calls and the provider is `echo`, add a `warnings` entry to the run
result metadata, e.g. `provider_echo_no_tool_calls`.

---

# DRAFT #2 — second issue to file on openminion/openminion (not yet filed)

**Title:** Freshness guardrail intercepts session-recall questions and refuses with a "regulation answer" message

**Affected version:** openminion 0.1.23, Ollama provider, llama3.1:8b

**Steps to reproduce**
1. Run an agent turn that performs tool work in a session (e.g. records
   triage decisions via tools).
2. In the same session, ask a recall question about that work:
   "From your triage in this session: which issue ids are release
   blockers, and what version are the release notes for? Answer briefly."
3. Observe the reply.

**Expected behavior**
The question is about the session's own local state — not about current
external facts — so it should be answered from session context without a
freshness classification.

**Actual behavior**
Reply: *"I couldn't provide a grounded current regulation answer because
Missing live-data evidence for a freshness-sensitive answer.. Please
retry with live data available so I can answer with sources and an exact
date."* The run metadata shows a `freshness_classify` LLM call; the
question was classified freshness-sensitive and the turn refused. (The
message also says "regulation", which has nothing to do with the task.)

**Impact**
Multi-turn agents that report back on their own work — a basic pattern
for release/QA assistants — lose their second turn to a false-positive
guardrail, with a confusing message.

**Evidence**
`logs/colab-verified/run-20261119T100600Z.json`, turn 2, in this project.
