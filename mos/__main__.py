"""CLI: python -m mos "add a note that says buy milk" [--mode both] [--serial emulator-5554]"""
from __future__ import annotations

import argparse
from pathlib import Path

from .agent import Agent
from .device import AdbDevice


def main() -> None:
    p = argparse.ArgumentParser(prog="mos", description="Run a Claude agent on an Android device via ADB.")
    p.add_argument("task", help="what the agent should do, in plain language")
    p.add_argument("--mode", default="both", choices=["screenshot", "tree", "both", "som"],
                   help="perception: screenshot only, accessibility tree only, both, or set-of-marks")
    p.add_argument("--serial", help="adb device serial (default: the only connected device)")
    p.add_argument("--max-steps", type=int, default=30)
    p.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--runs", default="runs", help="where to save screenshots and trajectories")
    p.add_argument("--yes", action="store_true", help="auto-approve irreversible actions (emulator only!)")
    a = p.parse_args()

    agent = Agent(AdbDevice(serial=a.serial), mode=a.mode, max_steps=a.max_steps,
                  effort=a.effort, run_root=Path(a.runs),
                  confirm=(lambda _msg: True) if a.yes else None)
    r = agent.run(a.task)
    print(f"\nRun saved to {r.run_dir}")


if __name__ == "__main__":
    main()
