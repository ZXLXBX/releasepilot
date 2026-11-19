#!/usr/bin/env python3
"""One-command run for ReleasePilot.

Usage:
    python run.py                 # auto provider: cortensor-portal if
                                  # CORTENSOR_API_KEY is set, else ollama
                                  # if its server answers, else echo (demo)
    RELEASEPILOT_PROVIDER=echo python run.py   # force the offline demo provider
    RELEASEPILOT_PROVIDER=ollama RELEASEPILOT_MODEL=llama3.2:1b python run.py

The run performs two turns in one OpenMinion session:
  turn 1 — triage all sample-repo issues, record them in SQLite, write
           output/RELEASE_NOTES.md
  turn 2 — recall, from the same session, which issues block the release

Everything the run produces is captured under logs/ (transcript, result
metadata, doctor diagnostics) so the demo is reproducible and auditable.
No secrets are read from or written to the repo: provider credentials come
from the environment only (e.g. CORTENSOR_API_KEY).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("OPENMINION_HOME", str(ROOT))
os.environ.setdefault("OPENMINION_DATA_ROOT", str(ROOT / ".openminion"))

# Environment fix (found during our first live run, see README "What broke"):
# some sandboxes export no_proxy with bracketed IPv6 entries like "[::1]",
# which httpx misparses ("InvalidURL: Invalid port: ':1]'") for *local*
# provider calls. Normalise the localhost entries to the plain form.
for _var in ("no_proxy", "NO_PROXY"):
    if _var in os.environ:
        _entries = [e.strip().strip("[]") for e in os.environ[_var].split(",") if e.strip()]
        os.environ[_var] = ",".join(dict.fromkeys(_entries))


def _ollama_up() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def choose_provider() -> tuple[str, str]:
    forced = os.environ.get("RELEASEPILOT_PROVIDER", "").strip().lower()
    if forced:
        defaults = {"ollama": "llama3.1:8b", "cortensor-portal": "oss-20b", "echo": "echo"}
        return forced, os.environ.get("RELEASEPILOT_MODEL", "") or defaults.get(forced, "")
    if os.environ.get("CORTENSOR_API_KEY"):
        return "cortensor-portal", os.environ.get("RELEASEPILOT_MODEL", "oss-20b")
    if _ollama_up():
        # llama3.1:8b is the SDK's recommended Ollama model — and in our
        # testing the only local model that completed the tool workflow
        # (1B-class models timed out or produced unusable tool calls).
        return "ollama", os.environ.get("RELEASEPILOT_MODEL", "llama3.1:8b")
    return "echo", "echo"


def ensure_config(provider: str, model: str) -> None:
    """Configure the default OpenMinion agent for the chosen provider."""
    cli = [sys.executable, "-m", "openminion"]
    if provider == "echo":
        cmd = cli + ["config", "init", "--force", "--provider", "echo"]
    else:
        cmd = cli + ["setup", "--provider", provider, "--no-chat"]
        if model:
            cmd += ["--model", model]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise SystemExit(f"provider setup failed ({provider}):\n{proc.stdout}\n{proc.stderr}")


def main() -> int:
    from openminion import Agent, ProviderError

    from releasepilot import INSTRUCTIONS, TOOLS

    provider, model = choose_provider()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    transcript: dict = {"provider": provider, "model": model or "(default)", "started": stamp, "turns": []}

    print(f"[releasepilot] provider={provider} model={model or '(default)'}")
    ensure_config(provider, model)

    session_id = f"releasepilot-{stamp}"

    # Permissions, exercised deliberately: OpenMinion's action policy pauses
    # before WRITE_SAFE tools. This operator callback approves only
    # ReleasePilot's own two write tools (and logs every decision); anything
    # else is denied. Without a callback the run would stop at the pause —
    # see docs/run-log.md for the transcript of exactly that happening.
    approved_tools = {"record_triage", "write_release_notes"}

    def approval_callback(tool_name: str, args: dict, approval_id: str) -> bool:
        decision = tool_name in approved_tools
        transcript.setdefault("approvals", []).append(
            {"tool": tool_name, "approval_id": approval_id, "approved": decision}
        )
        print(f"[approval] {tool_name}: {'approved' if decision else 'DENIED'}")
        return decision

    try:
        with Agent(
            instructions=INSTRUCTIONS,
            tools=TOOLS,
            session_id=session_id,
            model=model or None,
        ) as agent:
            turn1 = agent.run(
                "Prepare the next patch release for widgetkit: triage every open "
                "issue, record each triage decision, and write the release notes.",
                timeout_seconds=900,
                approval_callback=approval_callback,
            )
            # Fallback if a confirmation still surfaces as a paused thread:
            # answer it on the same thread, as the SDK docs prescribe.
            while turn1.metadata.get("respond_kind") == "policy_confirmation_prompt":
                print("[approval] answering paused confirmation on-thread")
                turn1 = agent.run(
                    "allow_session",
                    thread_id=turn1.thread_id,
                    timeout_seconds=900,
                    approval_callback=approval_callback,
                )
            transcript["turns"].append(
                {"prompt": "turn1: triage + release notes", "text": turn1.text,
                 "session_id": turn1.session_id, "run_id": turn1.run_id,
                 "run_state": turn1.run_state, "metadata": turn1.metadata, "stats": turn1.stats}
            )
            print("[turn 1]", turn1.text)

            turn2 = agent.run(
                "From your triage in this session: which issue ids are release "
                "blockers, and what version are the release notes for? Answer briefly.",
                timeout_seconds=300,
                approval_callback=approval_callback,
            )
            transcript["turns"].append(
                {"prompt": "turn2: session recall of blockers", "text": turn2.text,
                 "session_id": turn2.session_id, "run_id": turn2.run_id,
                 "run_state": turn2.run_state, "metadata": turn2.metadata, "stats": turn2.stats}
            )
            print("[turn 2]", turn2.text)
    except ProviderError as exc:
        transcript["error"] = f"{exc.code}: {exc.message}"
        print(f"[releasepilot] provider failed: {exc.code}: {exc.message}")
        (log_dir / f"run-{stamp}.json").write_text(json.dumps(transcript, indent=2, default=str))
        return 1

    # Diagnostics: doctor report for the configured agent.
    diag = subprocess.run(
        [sys.executable, "-m", "openminion", "doctor", "--json"],
        capture_output=True, text=True, timeout=120,
    )
    (log_dir / f"doctor-{stamp}.json").write_text(diag.stdout or diag.stderr)
    transcript["doctor_exit"] = diag.returncode
    (log_dir / f"run-{stamp}.json").write_text(json.dumps(transcript, indent=2, default=str))
    print(f"[releasepilot] logs written to logs/run-{stamp}.json and logs/doctor-{stamp}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
