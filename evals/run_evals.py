"""Run the task suite and score each run by device state, not by the agent's own claim.

    python -m evals.run_evals --server http://mos-gpu:8080 --token $MOS_TOKEN --repeats 3   # MAI-UI via the MOS server
    python -m evals.run_evals --modes screenshot tree both som --repeats 3                  # Claude baseline
    python -m evals.run_evals --server ... --only settings-dark-mode settings-wifi-off      # a subset

Each task has one or more checks: an `adb shell` command and what its output must be:
    "expect": exact output        "expect_contains": substring (case-insensitive)
    "expect_nonzero": not "" or "0"   "expect_changed": differs from the output before the run
All checks of a task must pass. Before the agent starts, the checks run once on the
fresh snapshot: if the task already looks done, the run is marked invalid (a dirty
snapshot or a broken check) and doesn't count.

Results go to runs/evals/<timestamp>/ (results.jsonl, summary.json); see evals/stats.py.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Callable

from evals.stats import summarize

Shell = Callable[[str], str]


EXPECT_KEYS = ("expect", "expect_contains", "expect_nonzero", "expect_changed")


def checks_of(task: dict) -> list[dict]:
    if "checks" in task:
        cs = task["checks"]
    else:
        cs = [{k: v for k, v in task.items() if k == "check" or k.startswith("expect")}]
    for c in cs:
        if not c.get("check") or sum(k in c for k in EXPECT_KEYS) != 1:
            raise ValueError(f"task {task.get('id')!r}: each check needs a command and exactly one of {EXPECT_KEYS}")
    if not cs:
        raise ValueError(f"task {task.get('id')!r} has no checks")
    return cs


def passes(c: dict, out: str, before: str | None = None) -> bool:
    out = out.strip()
    if "expect" in c:
        return out == c["expect"]
    if "expect_contains" in c:
        return c["expect_contains"].lower() in out.lower()
    if c.get("expect_nonzero"):
        return out not in ("", "0")
    if c.get("expect_changed"):
        return before is not None and out != before.strip() and out not in ("", "0")
    return False


def snapshot_outputs(shell: Shell, task: dict) -> list[str]:
    return [shell(c["check"]) for c in checks_of(task)]


def task_passes(shell: Shell, task: dict, before: list[str] | None = None) -> bool:
    cs = checks_of(task)
    before = before or [None] * len(cs)
    return all(passes(c, shell(c["check"]), b) for c, b in zip(cs, before))


def already_done(task: dict, before: list[str]) -> bool:
    """True if the fresh device already satisfies the task (checks that need a change can't be)."""
    cs = checks_of(task)
    if any(c.get("expect_changed") for c in cs):
        return False
    return all(passes(c, out) for c, out in zip(cs, before))


def load_tasks(path: str, only: list[str] | None = None) -> list[dict]:
    tasks = [json.loads(l) for l in open(path) if l.strip()]
    if only:
        missing = set(only) - {t["id"] for t in tasks}
        if missing:
            raise SystemExit(f"unknown task ids: {sorted(missing)}")
        tasks = [t for t in tasks if t["id"] in only]
    for t in tasks:
        checks_of(t)  # fail fast on a malformed task file
    return tasks


def git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    except OSError:
        return "?"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--tasks", default=str(Path(__file__).with_name("tasks.jsonl")))
    p.add_argument("--only", nargs="*", help="task ids to run")
    p.add_argument("--modes", nargs="+", default=["both"])
    p.add_argument("--repeats", type=int, default=1)
    p.add_argument("--snapshot", default="clean", help="emulator snapshot to load before each task ('' to skip)")
    p.add_argument("--serial")
    p.add_argument("--server", help="MOS server URL: evaluate the server's model instead of the Claude agent")
    p.add_argument("--token", default="")
    p.add_argument("--label", default="", help="free-text note saved with the results (e.g. 'hint on')")
    a = p.parse_args()
    if a.server:
        a.modes = ["server"]

    from mos.device import AdbDevice  # imported here so the scoring helpers above stay importable without adb
    tasks = load_tasks(a.tasks, a.only)
    dev = AdbDevice(serial=a.serial)
    # `; true`: a check like `grep -c` exits 1 when it counts 0, which adb passes back as an error.
    shell: Shell = lambda cmd: dev._shell(f"{cmd}; true")
    out_dir = Path("runs") / "evals" / time.strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = {"git": git_sha(), "server": a.server, "modes": a.modes, "repeats": a.repeats,
            "snapshot": a.snapshot, "label": a.label, "tasks": [t["id"] for t in tasks]}
    if a.server:
        try:
            import urllib.request
            with urllib.request.urlopen(a.server.rstrip("/") + "/healthz", timeout=10) as r:
                meta["model"] = json.loads(r.read()).get("model")
        except Exception as e:  # noqa: BLE001 - recorded, not fatal
            meta["model"] = f"unreachable: {e}"
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))

    rows = []
    with (out_dir / "results.jsonl").open("w") as f:
        for mode in a.modes:
            for t in tasks:
                for rep in range(a.repeats):
                    if a.snapshot:
                        subprocess.run(["adb"] + (["-s", a.serial] if a.serial else []) +
                                       ["emu", "avd", "snapshot", "load", a.snapshot], check=False)
                        time.sleep(2)  # let the restored UI settle
                    row = {"mode": mode, "task": t["id"], "rep": rep}
                    try:
                        before = snapshot_outputs(shell, t)
                    except Exception as e:  # noqa: BLE001 - device trouble shouldn't end the whole eval
                        row.update(invalid=True, success=False, note=f"pre-check failed: {e}")
                        before = None
                    if before is not None and already_done(t, before):
                        row.update(invalid=True, success=False, note="task already satisfied before the run")
                    elif before is not None:
                        t0 = time.time()
                        try:
                            row.update(run_agent(a, dev, mode, t))
                        except Exception as e:  # noqa: BLE001 - counted as a failed run, and we move on
                            row.update(status="error", self_report=False, note=f"run crashed: {e}")
                        row["seconds"] = round(time.time() - t0, 1)
                        try:
                            row["success"] = task_passes(shell, t, before)
                        except Exception as e:  # noqa: BLE001
                            row.update(invalid=True, success=False, note=f"check failed: {e}")
                    rows.append(row)
                    f.write(json.dumps(row) + "\n")
                    f.flush()
                    print(json.dumps(row))

    summary = {mode: summarize([r for r in rows if r["mode"] == mode]) for mode in a.modes}
    (out_dir / "summary.json").write_text(json.dumps({"meta": meta, "summary": summary}, indent=2))
    for mode, s in summary.items():
        print(f"{mode:10s} success={s['success_rate']:.0%} (95% CI {s['ci95'][0]:.0%}-{s['ci95'][1]:.0%})  "
              f"pass^k={s['pass_hat_k']:.0%}  false_success={s['false_success_rate']:.0%}  "
              f"avg_steps={s['avg_steps']}  invalid={s['invalid_runs']}")
    print(f"results in {out_dir}")


def run_agent(a, dev, mode: str, t: dict) -> dict:
    if a.server:
        from mos.remote import RemoteRunner, http_post
        runner = RemoteRunner(dev, http_post(a.server, a.token), confirm=lambda _r: True,
                              ask=lambda _q: "Use your best judgment.", log=lambda *_: None)
        status = runner.run(t["task"])
        return {"status": status, "self_report": status == "success", "steps": runner.steps,
                "session": runner.session_id}
    from mos.agent import Agent
    r = Agent(dev, mode=mode, run_root=Path("runs") / "evals" / mode / t["id"],
              confirm=lambda _m: True, ask=lambda _q: "Use your best judgment.").run(t["task"])
    return {"self_report": r.success, "steps": r.steps, **r.usage}


if __name__ == "__main__":
    main()
