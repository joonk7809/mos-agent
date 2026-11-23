# Progress log

Newest first. One entry per working session: what changed, what's verified, what's next, what's broken. Agents read the top few entries before starting (see the `ship` skill). Keep entries to a few lines; details belong in PRs.

## 2026-11-18 - overlay no longer eats agent taps
- The MAI-UI run on the emulator ended 15 of 54 runs "stopped": the panel is hidden in screenshots but stayed touchable, so taps aimed at the top of the screen hit Stop. The panel now passes touches through only while an agent stroke that starts on it runs (FLAG_NOT_TOUCHABLE), and is hidden for screenshots with alpha instead of INVISIBLE so a Stop pressed during a screenshot isn't swallowed.
- Also fixed: starting a task while a finished task's panel (with Close) was still up left the new task with no Stop button. Close now removes the panel at once, so it can't remove a new task's panel.
- Verified on the emulator with a scripted model that taps the Stop button's position, and with an injected human tap on Stop, bridge on and off (numbers in the PR).

## 2026-11-17 - emulator-first MOS (ADR 0010)
- joon picked the Pixel 7a (pager #6), then asked to work on emulators first. Added `rom/emulator/install_bridge.sh` (bridge as a platform-signed system app on a stock Google APIs emulator, $0), emulator targets in `rom/build.sh` (`sdk_phone_arm64`/`sdk_phone_x86_64`), a Gradle `:bridge` module sharing the ROM's Java (hidden API by reflection), and app-driven evals (`run_evals --driver app --bridge on|off`, EvalReceiver).
- Unverified here: everything on a device. CI compiles the app and the Gradle bridge.
- Next: joon runs the quick path and the bridge on/off A/B; then a box build of the emulator ROM.

## 2026-11-17 - MOS custom ROM (LineageOS) scaffolding
- joon chose "custom ROM now". Added `rom/`: one-command `rom/build.sh <codename>` for a rented build box (LineageOS 24.0 + TheMuppets blobs + private release keys), `vendor/extra` overlay with MosBridge (platform-signed priv-app: injected taps/swipes/keys/text, enables the agent's accessibility service; knownSigner-pinned to the MOS app key) and the preinstalled agent app. App uses the bridge when present, accessibility otherwise. ADRs 0008, 0009 (Pixel choice paged as #6, recommending 8a).
- Unverified: nothing here compiles in the cloud container. Java bridge and build script need a build-box run; Kotlin app changes compile in CI.
- Next: first build-box run, flash a Pixel, eval bridge vs accessibility on the same tasks.

## 2026-11-17 - agent loop, evals, GUI-Owl A/B prep (PR #3)
- Loop guard ends `stuck` runs; malformed actions retried at T=0.7; SFT export keeps user replies and hand-corrected args.
- Evals: multi-check tasks, dirty-snapshot detection, Wilson CI, pass^k, false-success rate; `evals.compare` for A/B. Emulator client sends phone-like JPEGs.
- `MOS_POLICY=guiowl` adds GUI-Owl-1.5-8B, context matched to its official agent (ADR 0008).
- Next: joon runs the A/B on emulator + rented GPU. Unverified: `dumpsys alarm` format, GUI-Owl fit on 24 GB.

## 2026-11-17 - agentic dev workflow
- Added `CLAUDE.md`, `.claude/` (hooks, 4 subagents, 6 skills), `scripts/check.sh`, CI lint/type gates, Hypothesis property tests, ADR log, PR template.
- Property tests found and fixed: dash-separated card numbers skipped confirmation (`mos/safety.py`); non-numeric/NaN model coordinates raised TypeError (`mos/maiui.py`).
- Next: device-level eval baseline for MAI-UI-8B (needs emulator + GPU, see `run-evals`); stateful test of the session lifecycle.
- Adversarial review (fresh subagent): FIX FIRST -> fixed. Bash guard now parses refspecs (blocks `+main`, `x:main`; allows `fix-main-bug`), card check uses digit groups (no longer flags dates/phones), hook tests in `tests/test_claude_hooks.py`.

## 2026-11-23 - Dot v0 (local Mac hotkey agent, side project)
- Research doc: https://claude.ai/artifact/952W6ZsVNQ6Q1ro4oSNdkG ("Inklink Mini 2" read as Thinking Machines' Inkling 2 Mini model).
- Added `dot/`: agent loop + tools (shell, files, AppleScript, web, AX elements, screenshot/click/type/scroll/drag), JSON-lines daemon, Swift dot + Option+Space prompt bar, timing stats. Unbound by joon's decision (ADR 0011).
- Next: first run on joon's Mac (24 GB, 4-bit GGUF); measure seconds per step with `python -m dot.stats`; pin Inkling's coordinate format.
