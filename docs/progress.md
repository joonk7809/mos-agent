# Progress log

Newest first. One entry per working session: what changed, what's verified, what's next, what's broken. Agents read the top few entries before starting (see the `ship` skill). Keep entries to a few lines; details belong in PRs.

## 2026-11-17 - agent loop, evals, GUI-Owl A/B prep (PR #3)
- Loop guard ends `stuck` runs; malformed actions retried at T=0.7; SFT export keeps user replies and hand-corrected args.
- Evals: multi-check tasks, dirty-snapshot detection, Wilson CI, pass^k, false-success rate; `evals.compare` for A/B. Emulator client sends phone-like JPEGs.
- `MOS_POLICY=guiowl` adds GUI-Owl-1.5-8B, context matched to its official agent (ADR 0007).
- Next: joon runs the A/B on emulator + rented GPU. Unverified: `dumpsys alarm` format, GUI-Owl fit on 24 GB.

## 2026-11-17 - agentic dev workflow
- Added `CLAUDE.md`, `.claude/` (hooks, 4 subagents, 6 skills), `scripts/check.sh`, CI lint/type gates, Hypothesis property tests, ADR log, PR template.
- Property tests found and fixed: dash-separated card numbers skipped confirmation (`mos/safety.py`); non-numeric/NaN model coordinates raised TypeError (`mos/maiui.py`).
- Next: device-level eval baseline for MAI-UI-8B (needs emulator + GPU, see `run-evals`); stateful test of the session lifecycle.
- Adversarial review (fresh subagent): FIX FIRST -> fixed. Bash guard now parses refspecs (blocks `+main`, `x:main`; allows `fix-main-bug`), card check uses digit groups (no longer flags dates/phones), hook tests in `tests/test_claude_hooks.py`.
