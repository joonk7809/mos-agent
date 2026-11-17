"""The Claude Code hooks in .claude/hooks are part of the verification gate; test them like code."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

GUARD = Path(__file__).parent.parent / ".claude" / "hooks" / "pre_bash_guard.py"


def guard(cmd: str) -> int:
    r = subprocess.run([sys.executable, str(GUARD)], input=json.dumps({"tool_input": {"command": cmd}}),
                       capture_output=True, text=True, cwd="/")  # cwd outside git: current branch unknown
    return r.returncode


@pytest.mark.parametrize("cmd", [
    "git push origin main", "git push --force origin main", "git push -f origin master", "git push origin +main",
    "git push origin feature:main", "git push origin main:main", "git push origin main --tags",
    "git push origin HEAD:refs/heads/main", "cd x && git push origin main",
    "git commit --no-verify -m x", "rm -rf /", "rm -r -f /", "rm -fr ~",
    "cat .env", "echo $MOS_TOKEN", "env | grep MOS_TOKEN", "printenv",
    "python3 -c 'import os; print(os.environ[\"MOS_TOKEN\"])'",
])
def test_guard_blocks(cmd):
    assert guard(cmd) == 2


@pytest.mark.parametrize("cmd", [
    "git push -u origin claude/project-thread-cgs8u2", "git push --force-with-lease origin fix-main-bug",
    "git push --force-with-lease origin claude/fix-main-activity", "git push origin feat --force && git log main",
    "git log main", "git diff origin/main...HEAD", "git checkout main", "grep MOS_TOKEN mos/server.py",
    'git commit -m "docs: mention --no-verify policy"', "cat .env.example", "rm -rf build/", "python -m pytest -q",
])
def test_guard_allows_routine_work(cmd):
    assert guard(cmd) == 0
