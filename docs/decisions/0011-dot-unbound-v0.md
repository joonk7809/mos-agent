# 0011. Ship the Mac "Dot" agent unbound first; add constraints where failures show up

- Status: Accepted
- Date: 2026-11-23
- Deciders: joon (project thread, "lets do unbound first. setting constraints later where failures arise is easier"); agent (what v0 still keeps)

## Context
Dot (`dot/`) is a side project in this repo: a local Mac agent behind a hotkey, running Inkling 2 Mini
(Thinking Machines, Nov 2026) on joon's own machine. The research doc designed a local-first permission model:
OS-enforced default-deny egress, gated outbound tools, and web modes (Sealed / Research / Online). joon chose to
build without any of it first and add constraints where real failures appear, later possibly with a reviewer
model like OpenAI Codex's auto-review.

## Decision
We will ship Dot v0 with full user-level access (shell, files, AppleScript, web, screen control) and no
confirmation gates. v0 keeps only things that don't constrain it: Stop (halts before the next action), a turn
cap, a repeat guard, an end-on-model-failure rule, and a JSONL action log of every step, which is the input for
writing constraints later. The MOS phone invariants in `CLAUDE.md` (safety gate, `runs/` handling, emulator-only
tests) apply to the phone agent, not to Dot; nothing in `mos/` changes.

## Alternatives considered
- **Build the 0c/0d permission model first** - safer, but joon wants to learn where failures actually happen before designing gates.
- **Gate only sends/deletes (MOS-style) in v0** - joon explicitly asked for unbound.

## Consequences
- A prompt injection in a page, file or message can make v0 leak data or damage files. Accepted for v0; it's the first thing a reviewer/gate should cover.
- Revisit when the action log shows a harmful or unwanted action, before giving Dot to anyone else, or before turning on a hosted-model fallback (which sends screenshots off the machine).
