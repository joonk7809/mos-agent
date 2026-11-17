"""Run the task suite in each perception mode and score with on-device checks.

    python -m evals.run_evals --server http://localhost:8080 --token $MOS_TOKEN   # MAI-UI via the MOS server
    python -m evals.run_evals --modes screenshot tree both som --repeats 3       # Claude baseline

Each check is an `adb shell` command whose output is compared to the task's
expectation, so success is judged by device state, not by the agent's own claim.
Reset the emulator to a snapshot between runs for clean comparisons:
    adb emu avd snapshot load clean
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from mos.agent import Agent
from mos.device import AdbDevice
from mos.remote import RemoteRunner, http_post


def check(dev: AdbDevice, t: dict) -> bool:
    out = dev._shell(t["check"]).strip()
    if "expect" in t:
        return out == t["expect"]
    if "expect_contains" in t:
        return t["expect_contains"].lower() in out.lower()
    if t.get("expect_nonzero"):
        return out not in ("", "0")
    return False


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--tasks", default=str(Path(__file__).with_name("tasks.jsonl")))
    p.add_argument("--modes", nargs="+", default=["both"])
    p.add_argument("--repeats", type=int, default=1)
    p.add_argument("--snapshot", default="clean", help="emulator snapshot to load before each task ('' to skip)")
    p.add_argument("--serial")
    p.add_argument("--server", help="MOS server URL: evaluate the server's model instead of the Claude agent")
    p.add_argument("--token", default="")
    a = p.parse_args()
    if a.server:
        a.modes = ["server"]

    tasks = [json.loads(l) for l in Path(a.tasks).read_text().splitlines() if l.strip()]
    dev = AdbDevice(serial=a.serial)
    rows = []
    for mode in a.modes:
        for t in tasks:
            for rep in range(a.repeats):
                if a.snapshot:
                    subprocess.run(["adb", "emu", "avd", "snapshot", "load", a.snapshot], check=False)
                if a.server:
                    runner = RemoteRunner(dev, http_post(a.server, a.token), confirm=lambda _r: True,
                                          ask=lambda _q: "Use your best judgment.", log=lambda *_: None)
                    status = runner.run(t["task"])
                    extra = {"self_report": status == "success", "steps": 0}
                else:
                    r = Agent(dev, mode=mode, run_root=Path("runs") / "evals" / mode / t["id"],
                              confirm=lambda _m: True, ask=lambda _q: "Use your best judgment.").run(t["task"])
                    extra = {"self_report": r.success, "steps": r.steps, **r.usage}
                ok = check(dev, t)
                rows.append({"mode": mode, "task": t["id"], "rep": rep, "success": ok, **extra})
                print(json.dumps(rows[-1]))
    Path("runs").mkdir(exist_ok=True)
    Path("runs/eval_results.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    for mode in a.modes:
        mine = [r for r in rows if r["mode"] == mode]
        sr = sum(r["success"] for r in mine) / len(mine)
        print(f"{mode:10s} success={sr:.0%}  avg_steps={sum(r['steps'] for r in mine)/len(mine):.1f}")


if __name__ == "__main__":
    main()
