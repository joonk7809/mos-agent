# 0005. Gate every change on automated checks plus independent agent review

- Status: Accepted
- Date: 2026-11-17
- Deciders: agent (agentic dev workflow setup)

## Context
joon wants agents to work autonomously for long stretches. Agents stop when work *looks* done; a generator grading its own work is lenient (Anthropic, "Harness design for long-running application development"). The code drives real phones, so silent regressions in parsing or the safety gate are costly.

## Decision
- One gate command, `scripts/check.sh` (ruff, mypy, pytest incl. Hypothesis property tests), used by the Claude Stop hook, locally, and in CI.
- Claude Code hooks enforce it deterministically (lint on edit, block stopping while red, block force-push/--no-verify/secret printing).
- Non-trivial changes get a fresh-context `adversarial-reviewer` (and `safety-reviewer` where relevant) before the PR; findings must come with a repro to count as confirmed; style nits are out of scope.
- Property-based tests are our "formal verification" rung; heavier methods (stateful models, TLA+) need an ADR showing a bug class escaping lighter ones.

## Alternatives considered
- **Advisory CLAUDE.md rules only** - ignored under context pressure; hooks are deterministic.
- **Full formal verification now** - cost far above value for a ~2k-line prototype.
- **LLM review in CI (claude-code-action)** - needs an API key secret and per-PR spend; can be added later if in-session review proves insufficient.

## Consequences
Adding the property tests immediately found two real bugs: dash-separated card numbers bypassed the confirmation gate, and non-numeric coordinates from the model raised TypeError instead of ParseError. Turns that change Python take a few seconds longer.
