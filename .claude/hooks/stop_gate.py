#!/usr/bin/env python3
"""Stop: don't let a turn end with Python changes that fail scripts/check.sh.

Only runs when tracked Python/config files differ from HEAD (or new .py files exist),
so conversational turns stay instant. Blocks once; if Claude is already continuing
because of this hook (stop_hook_active), it lets the turn end so we never loop.
Set MOS_SKIP_STOP_GATE=1 to disable (e.g. while deliberately mid-refactor).
"""
import json
import os
import subprocess
import sys

data = json.load(sys.stdin)
if data.get("stop_hook_active") or os.environ.get("MOS_SKIP_STOP_GATE"):
    sys.exit(0)
root = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())


def git(*args: str) -> str:
    r = subprocess.run(["git", *args], capture_output=True, text=True, cwd=root)
    return r.stdout if r.returncode == 0 else ""


changed = git("diff", "--name-only", "HEAD").split() + git("ls-files", "--others", "--exclude-standard").split()
relevant = [f for f in changed if f.endswith((".py", "pyproject.toml", "pytest.ini", "requirements.txt"))]
if not relevant:
    sys.exit(0)
r = subprocess.run(["bash", "scripts/check.sh"], capture_output=True, text=True, cwd=root)
if r.returncode != 0:
    out = (r.stdout + r.stderr)[-4000:]
    print(json.dumps({"decision": "block", "reason":
                      "scripts/check.sh fails on your uncommitted changes. Fix the root cause "
                      "(don't skip or weaken tests), or say plainly why you're stopping with it red.\n\n" + out}))
sys.exit(0)
