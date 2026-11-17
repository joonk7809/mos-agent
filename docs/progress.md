# Progress log

Newest first. One entry per working session: what changed, what's verified, what's next, what's broken. Agents read the top few entries before starting (see the `ship` skill). Keep entries to a few lines; details belong in PRs.

## 2026-11-17 - agentic dev workflow
- Added `CLAUDE.md`, `.claude/` (hooks, 4 subagents, 6 skills), `scripts/check.sh`, CI lint/type gates, Hypothesis property tests, ADR log, PR template.
- Property tests found and fixed: dash-separated card numbers skipped confirmation (`mos/safety.py`); non-numeric/NaN model coordinates raised TypeError (`mos/maiui.py`).
- Next: device-level eval baseline for MAI-UI-8B (needs emulator + GPU, see `run-evals`); stateful test of the session lifecycle.
