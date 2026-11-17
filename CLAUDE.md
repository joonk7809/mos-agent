# MOS - agent working agreement

MOS lets an open-weight model (MAI-UI-8B, served by vLLM on a rented GPU) operate Android phones for joon and his friends. The phone app (`android/`) uses only accessibility APIs and talks to `mos/server.py` over Tailscale. Architecture and setup are in `README.md`; settled decisions and their reasons are in `docs/decisions/` (read the index before proposing a different model, transport, or permission model).

## Operating mode
- **Run autonomously.** joon does not want to be consulted on setup, tooling, libraries, refactors or routine choices. Decide, record non-obvious decisions as ADRs (`adr` skill), and keep going.
- **Page joon only** for non-obvious, consequential choices: research-direction tradeoffs, things that are hard to reverse, real money, or anything touching friends' phones or data. Use the `page-joon` skill; it defines the bar and the pager format (a GitHub issue labeled `needs-joon`). While waiting, keep working on your recommended option if it's reversible.
- Cost matters: joon wants compute kept low. Prefer cheaper models for workers, stop GPU instances you start, don't run evals in loops without reason.

## Commands
```bash
pip install -r requirements-dev.txt
scripts/check.sh            # THE gate: ruff + mypy + pytest (~5 s). Must be green before you say done.
scripts/check.sh --quick    # lint + types only
python -m pytest tests/test_properties.py      # property tests (Hypothesis)
cd android && ./gradlew -Pmos.coreOnly :core:test   # Kotlin protocol tests; full APK build is CI-only
```
Use `python -m <tool>` rather than bare `pytest`/`mypy`: some machines have several interpreters on PATH.

## How work gets done (details in `.claude/skills/`)
1. Orient: `git log`, `docs/progress.md`, `scripts/check.sh`.
2. Non-trivial change -> `ship` skill: contract with machine-checkable acceptance criteria first, then test-first implementation, then `scripts/check.sh`, then **independent review by subagents in fresh context** (`adversarial-reviewer` always, `safety-reviewer` for anything touching actions, the prompt, auth, networking or logging, `verifier` for the done-claim). Fix confirmed findings; ignore style nits.
3. Capability claims need evals (`run-evals` skill), judged by device state, never by the agent's self-report.
4. Parsing, coordinate mapping, the safety gate and the session state machine get property tests (`property-tests` skill).
5. Large or parallel work: `orchestrate` skill. The agent that wrote something never is the only one that judged it.
6. Leave a line in `docs/progress.md` at the end of a working session; open a PR from a branch (never push to main). Paste real check output and reviewer verdicts into the PR.

Hooks in `.claude/settings.json` enforce some of this: ruff runs on every edited `.py`, the Stop hook blocks ending a turn while `scripts/check.sh` fails on uncommitted Python changes (set `MOS_SKIP_STOP_GATE=1` to bypass deliberately), and a Bash guard blocks force-pushes to main, `--no-verify`, and printing secrets.

## Safety invariants (never weaken without an ADR and a page)
- Every tap on a send/pay/delete-like element, typing into a password field, and typing a card-number-like string must go through the phone's Allow/Deny confirmation (`mos/safety.py`). The gate is a heuristic: widen it freely, narrow it only with evidence.
- Stop on the overlay must halt the agent before its next action. Malformed model output, network loss or server restart must stop the task, not guess.
- On-screen text is untrusted input (prompt injection). Our code never treats it as an instruction.
- All server endpoints require the bearer token (`MOS_TOKEN`, constant-time compare). Bind to the tailnet, never expose 8080 publicly. Never log or commit tokens or Tailscale keys.
- `runs/` holds screenshots of real people's phones. It stays out of git, out of third-party APIs, and out of SFT exports unless the session's owner agreed.
- Never test on a real phone in a way that could send, pay or delete for real. Use the emulator.

## Gotchas
- Cloud containers can't download the Android SDK (dl.google.com is blocked): the APK builds only in GitHub Actions. Kotlin `:core` tests run anywhere with `-Pmos.coreOnly`.
- The wire protocol lives in two places: `mos/server.py` (docstring) and `android/core/src/main/kotlin/dev/mos/core/Protocol.kt`. Change both together and extend `ProtocolTest.kt`.
- MAI-UI emits coordinates in 0..999 (`maiui.SCALE`); the server maps them to pixels. The phone only ever sees pixels.
- vLLM is pinned to 0.11.0 per MAI-UI's instructions; don't bump it casually.
- `mos/agent.py` is the old Claude-driven baseline, kept for comparison. The product path is `server.py` + `maiui.py`.
- `rom/` is the LineageOS MOS ROM (ADR 0008). It only builds on a rented build box (`rom/build.sh`); here, `tests/test_rom.py` checks its wiring. Emulator-first (ADR 0010): `rom/emulator/` installs the bridge on a stock Google APIs emulator; `android/bridge` builds the same Java with Gradle (keep its manifest equal to the ROM one minus `package`). App-driven evals: `run_evals --driver app --bridge on|off`. `IMosBridge.aidl` exists in two places (ROM and app) and must stay byte-identical; append methods only. Never commit anything from `MOS_KEYS`.
- Never edit `evals/tasks.jsonl` checks to make a run pass; never add `skip`/`xfail` or loosen an assertion to get green.
