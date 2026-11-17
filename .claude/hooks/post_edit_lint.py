#!/usr/bin/env python3
"""PostToolUse(Edit|Write): lint the Python file Claude just changed and feed problems back.

Exit 2 shows stderr to Claude so it fixes the problem in the same turn.
Fast (ruff on one file); the full gate runs in stop_gate.py.
"""
import json
import os
import subprocess
import sys

data = json.load(sys.stdin)
path = (data.get("tool_input") or {}).get("file_path") or ""
root = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
if not path.endswith(".py") or not os.path.isfile(path):
    sys.exit(0)
if os.path.commonpath([os.path.realpath(path), os.path.realpath(root)]) != os.path.realpath(root):
    sys.exit(0)
r = subprocess.run([sys.executable, "-m", "ruff", "check", "--fix", path], capture_output=True, text=True, cwd=root)
if r.returncode == 1:  # 1 = lint findings remain; other codes mean ruff itself is missing/broken: don't block
    print(f"ruff found problems in {path}:\n{r.stdout[-3000:]}", file=sys.stderr)
    sys.exit(2)
sys.exit(0)
