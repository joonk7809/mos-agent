#!/usr/bin/env python3
"""PreToolUse(Bash): deny commands that bypass the repo's safety rails.

Deterministic backstop for rules in CLAUDE.md. Exit 2 blocks and tells Claude why.
Parses each simple command with shlex instead of regex-matching the whole line, so
quoted commit messages and branch names like `fix-main-bug` don't trip it.
"""
import json
import re
import shlex
import subprocess
import sys

PROTECTED = {"main", "master"}
SECRETS = ("MOS_TOKEN", "TS_AUTHKEY", "ANTHROPIC_API_KEY", "GH_TOKEN", "GITHUB_TOKEN")
PRINTERS = {"echo", "printf", "printenv", "env", "cat", "less", "more", "head", "tail", "set", "export"}


def segments(cmd: str) -> list[list[str]]:
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=";&|")
        lex.whitespace_split = True
        toks = list(lex)
    except ValueError:  # unbalanced quotes: fall back to a plain split
        toks = cmd.split()
    out, cur = [], []
    for t in toks:
        if t and set(t) <= set(";&|"):
            out.append(cur)
            cur = []
        else:
            cur.append(t)
    out.append(cur)
    return [s for s in out if s]


def current_branch() -> str:
    r = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True)
    return r.stdout.strip()


def check_push(args: list[str]) -> str | None:
    positional = [a for a in args if not a.startswith("-")]
    refspecs = positional[1:]  # first positional is the remote
    if not refspecs:
        return "Pushing while on main is not allowed." if current_branch() in PROTECTED else None
    for spec in refspecs:
        dst = spec.lstrip("+").split(":")[-1].removeprefix("refs/heads/")
        if dst == "HEAD" and ":" not in spec:
            dst = current_branch()
        if dst in PROTECTED:
            return "Push to a branch and open a PR; never push to main directly."
    return None


def check(seg: list[str]) -> str | None:
    while seg and re.fullmatch(r"\w+=.*", seg[0]):  # leading VAR=value assignments
        seg = seg[1:]
    if not seg:
        return None
    prog, args = seg[0].rsplit("/", 1)[-1], seg[1:]
    if prog == "git" and args:
        if "--no-verify" in args:
            return "Don't bypass git hooks with --no-verify; fix what the hook reports."
        if args[0] == "push":
            return check_push(args[1:])
    if prog == "rm":
        flags = "".join(a.lstrip("-") for a in args if a.startswith("-") and not a.startswith("--"))
        recursive = "r" in flags.lower() or "--recursive" in args
        if recursive and any(a in ("/", "/*", "~", "~/", "$HOME", "${HOME}") for a in args):
            return "Refusing to recursively delete / or $HOME."
    if prog in PRINTERS or (prog in ("python", "python3") and "-c" in args):
        text = " ".join(args)
        if any(s in text for s in SECRETS) or (prog in ("printenv", "env", "set", "export") and not args):
            return "Don't print secrets or the whole environment into the transcript."
        if prog in PRINTERS and any(re.fullmatch(r"(.*/)?\.env(\.local|\.prod\w*)?", a) for a in args):
            return "Don't print .env files into the transcript."
    return None


cmd = (json.load(sys.stdin).get("tool_input") or {}).get("command") or ""
for seg in segments(cmd):
    why = check(seg)
    if why:
        print(f"Blocked by .claude/hooks/pre_bash_guard.py: {why}", file=sys.stderr)
        sys.exit(2)
sys.exit(0)
