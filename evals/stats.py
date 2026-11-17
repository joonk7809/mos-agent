"""Summary statistics for eval runs.

A phone agent is only useful if it's reliable, so besides the success rate we report:
- a Wilson 95% interval, because with ~10 tasks x 3 repeats a few points of
  difference between two configs is usually noise;
- pass^k (all k repeats of a task succeed), the reliability a user feels, next to
  pass@k (any repeat succeeds), the capability ceiling;
- false success: runs where the agent said it succeeded but the device check
  failed. Those are the worst failures, since the user is told it's done.
"""
from __future__ import annotations

import math
from collections import defaultdict


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def summarize(rows: list[dict]) -> dict:
    """Rows are dicts with at least task, success (bool); optionally self_report, steps, seconds, invalid."""
    valid = [r for r in rows if not r.get("invalid")]
    n = len(valid)
    wins = sum(bool(r["success"]) for r in valid)
    by_task: dict[str, list[bool]] = defaultdict(list)
    for r in valid:
        by_task[r["task"]].append(bool(r["success"]))
    # pass@k / pass^k only over tasks with all k repeats valid, so one lucky valid run
    # can't stand in for k, and a task whose runs were all invalid still shows up as missing.
    tasks = {r["task"] for r in rows}
    k = max((len(v) for v in by_task.values()), default=0)
    full = {t: v for t, v in by_task.items() if len(v) == k}
    claimed = [r for r in valid if r.get("self_report")]
    false_success = sum(1 for r in claimed if not r["success"])
    mean = lambda key: round(sum(r.get(key) or 0 for r in valid) / n, 2) if n else 0.0
    lo, hi = wilson(wins, n)
    return {
        "runs": n,
        "invalid_runs": len(rows) - n,
        "success_rate": round(wins / n, 3) if n else 0.0,
        "ci95": [round(lo, 3), round(hi, 3)],
        "pass_at_k": round(sum(any(v) for v in full.values()) / len(full), 3) if full else 0.0,
        "pass_hat_k": round(sum(all(v) for v in full.values()) / len(full), 3) if full else 0.0,
        "k": k,
        "tasks_with_k_valid_runs": f"{len(full)}/{len(tasks)}",
        "false_success_rate": round(false_success / len(claimed), 3) if claimed else 0.0,
        "avg_steps": mean("steps"),
        "avg_seconds": mean("seconds"),
        "per_task": {t: f"{sum(by_task.get(t, []))}/{len(by_task.get(t, []))}" for t in sorted(tasks)},
    }
