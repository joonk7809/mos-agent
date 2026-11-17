---
name: orchestrate
description: How to split MOS work across subagents, parallel sessions and project threads without losing quality - orchestrator/worker briefs, fan-out, worktree isolation, generator/evaluator separation. Use when a task has independent parts, needs wide reading, or will run for hours.
---

# Orchestrating agents

Context is the scarce resource; a fresh context is also the best reviewer. Delegate to protect both, not by default: every handoff loses detail.

## When to fan out
- Independent parts that don't edit the same files (e.g. server change + eval tasks + docs) -> parallel subagents, each with `isolation: worktree` if they edit code.
- Wide reading (survey papers, sweep the Android codebase) -> `researcher` or Explore subagent; keep only its conclusion.
- Judging work -> always a different agent than the one that produced it (`adversarial-reviewer`, `verifier`, `safety-reviewer`). The generator grading itself is the failure mode to avoid.
- Separate goals that will take more than a session (research track, pager, product scoping) -> separate project threads. Threads message each other only when one's work depends on the other's.

Don't fan out for a handful of tool calls or a lookup whose location you already know.

## Writing a brief (the worker sees nothing else)
- Goal and why, in two sentences.
- Exact scope: files/dirs it may touch, what's out of scope.
- What you already know or ruled out, so it doesn't redo it.
- The acceptance check it must run and the output format you want back (verdict first, evidence after).
- Budget: rough turn limit, and "stop and report" conditions.
Give reviewers the diff and criteria, not your conclusion: an agent handed your hypothesis tends to return it confirmed.

## Long-running work (hours, many sessions)
- Keep state in files, not in context: `docs/progress.md` (what was done, what's next, what's broken), the PR description's contract, ADRs.
- One feature at a time; leave main mergeable after every session; commit at each green step.
- Start every session with the orient step of the `ship` skill so a broken state from the last session surfaces before new work.
- Rely on the Stop hook and `scripts/check.sh` as the gate, not on remembering to test.

## Limits
Subagents can nest about three levels deep and up to ~20 run concurrently; past 3-5 parallel workers the merge and review cost usually outweighs the speedup here. Cost matters on this project (joon wants compute kept low), so prefer `sonnet` workers and reserve `opus` for review and hard design.
