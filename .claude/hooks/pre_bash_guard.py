#!/usr/bin/env python3
"""PreToolUse(Bash): deny commands that bypass the repo's safety rails.

Deterministic backstop for rules in CLAUDE.md. Exit 2 blocks and tells Claude why.
"""
import json
import re
import sys

cmd = (json.load(sys.stdin).get("tool_input") or {}).get("command") or ""
RULES = [
    (r"git\s+push\b.*(--force\b|--force-with-lease\b|\s-f\b).*\b(main|master)\b|git\s+push\b.*\b(main|master)\b.*(--force\b|\s-f\b)",
     "Force-pushing main is not allowed."),
    (r"git\s+push\b[^|;&]*\sorigin\s+(HEAD:)?(main|master)\s*($|[;&|])", "Push to a branch and open a PR; never push to main directly."),
    (r"--no-verify\b", "Don't bypass git hooks with --no-verify; fix what the hook reports."),
    (r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f?\s+(/|~|\$HOME)(\s|$)", "Refusing to recursively delete / or $HOME."),
    (r"(cat|less|head|tail|printenv)\b.*(MOS_TOKEN|TS_AUTHKEY|ANTHROPIC_API_KEY|\.env\b)", "Don't print secrets into the transcript."),
]
for pattern, why in RULES:
    if re.search(pattern, cmd):
        print(f"Blocked by .claude/hooks/pre_bash_guard.py: {why}", file=sys.stderr)
        sys.exit(2)
sys.exit(0)
