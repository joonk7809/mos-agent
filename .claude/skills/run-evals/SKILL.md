---
name: run-evals
description: Measure whether the agent actually got better - run the device eval suite against the emulator and the MOS server, and log results. Use after changes to prompting, the model, the server's action mapping, safety gating, or the Android client, and before claiming any capability improvement.
---

# Run evals

Three levels; use the highest one the machine you're on can run, and say which one you ran.

| Level | Needs | Command | Proves |
|---|---|---|---|
| Offline | nothing | `scripts/check.sh` | protocol, parsing, safety gate, server loop with a scripted model and `FakeDevice` |
| Device | emulator with snapshot `clean`, MOS server (GPU) | `python -m evals.run_evals --server http://mos-gpu:8080 --token $MOS_TOKEN --repeats 3` | task success judged by device state (`adb shell` checks), not the agent's own claim |
| Real phone | joon's/friends' phones | manual, see README | only joon runs these |

Cloud containers can't run the device level (no emulator, no tailnet). Hand that to a session on joon's machine or note it as unverified.

Rules:
- Success is what the `check` in `evals/tasks.jsonl` says, never the agent's self-report. Report both; a gap between them is a finding in itself.
- Never edit a task's `check`/`expect` to make a run pass. Fixing a genuinely wrong check is a separate commit with the reason in its message.
- Use `--repeats 3` or more before comparing two variants; a single run per task is noise at this suite size. Report success as k/n per task.
- Add a task when you fix a failure mode, so it stays fixed.
- Log every device-level run as one row in `docs/evals-log.md`: date, commit, model and server settings, k/n per task, total, notes. Raw rows stay in `runs/eval_results.jsonl` (gitignored).
