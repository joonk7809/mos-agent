"""Compare two eval runs, e.g. MAI-UI vs GUI-Owl, and say whether the gap is real.

    python -m evals.compare runs/evals/20261118-101500 runs/evals/20261118-140200

Tasks are the unit: each task's success rate in run A is paired with the same task's
rate in run B, and a paired bootstrap over tasks gives a 95% interval for the
difference. With ~10 tasks x 3 repeats only big gaps (roughly 20+ points) will be
called; anything else is reported as too close to call.
"""
from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

from evals.stats import summarize


def load(run_dir: Path) -> tuple[dict, list[dict]]:
    meta = json.loads((run_dir / "meta.json").read_text()) if (run_dir / "meta.json").exists() else {}
    rows = [json.loads(l) for l in (run_dir / "results.jsonl").read_text().splitlines() if l.strip()]
    return meta, rows


def per_task(rows: list[dict]) -> dict[str, float]:
    by: dict[str, list[bool]] = defaultdict(list)
    for r in rows:
        if not r.get("invalid"):
            by[r["task"]].append(bool(r["success"]))
    return {t: sum(v) / len(v) for t, v in by.items()}


def paired_bootstrap(a: dict[str, float], b: dict[str, float], n: int = 10000, seed: int = 0) -> dict:
    """Difference B - A in mean per-task success, over tasks valid in both runs."""
    tasks = sorted(set(a) & set(b))
    if not tasks:
        return {"tasks": 0, "diff": 0.0, "ci95": [0.0, 0.0], "verdict": "no tasks in common"}
    diffs = [b[t] - a[t] for t in tasks]
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(diffs) for _ in tasks) / len(tasks) for _ in range(n))
    lo, hi = means[int(0.025 * n)], means[int(0.975 * n) - 1]
    d = sum(diffs) / len(diffs)
    verdict = "B is better" if lo > 0 else "A is better" if hi < 0 else "too close to call"
    return {"tasks": len(tasks), "diff": round(d, 3), "ci95": [round(lo, 3), round(hi, 3)], "verdict": verdict}


def main() -> None:
    p = argparse.ArgumentParser(prog="evals.compare")
    p.add_argument("a", type=Path)
    p.add_argument("b", type=Path)
    x = p.parse_args()
    (ma, ra), (mb, rb) = load(x.a), load(x.b)
    name = lambda m, d: m.get("label") or m.get("policy") or d.name
    na, nb = name(ma, x.a), name(mb, x.b)
    if ma.get("screenshots") != mb.get("screenshots"):
        print("warning: the runs sent screenshots differently; results may not be comparable")
    sa, sb = summarize(ra), summarize(rb)
    for n, s in ((na, sa), (nb, sb)):
        print(f"{n:24s} success={s['success_rate']:.0%} (95% CI {s['ci95'][0]:.0%}-{s['ci95'][1]:.0%})  "
              f"pass^k={s['pass_hat_k']:.0%}  false_success={s['false_success_rate']:.0%}  avg_steps={s['avg_steps']}")
    ta, tb = per_task(ra), per_task(rb)
    print("\nper task (A -> B):")
    for t in sorted(set(ta) | set(tb)):
        fa = f"{ta[t]:.0%}" if t in ta else "-"
        fb = f"{tb[t]:.0%}" if t in tb else "-"
        print(f"  {t:28s} {fa:>5s} -> {fb:>5s}")
    r = paired_bootstrap(ta, tb)
    print(f"\n{nb} - {na}: {r['diff']:+.0%} (95% CI {r['ci95'][0]:+.0%} to {r['ci95'][1]:+.0%}, {r['tasks']} tasks): {r['verdict']}")


if __name__ == "__main__":
    main()
