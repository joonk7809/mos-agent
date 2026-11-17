# How agents work on MOS (and why)

`CLAUDE.md` is the short operational version. This page records the reasoning and sources so the setup can be revised deliberately when models or tools change. Harness pieces encode assumptions about model limits, and those assumptions go stale; prune pieces that stop earning their keep.

## The loop
```
orient ─▶ contract ─▶ implement (test-first) ─▶ scripts/check.sh ─▶ independent review ─▶ PR
  ▲          (machine-checkable          │  hooks: lint on edit,        (fresh-context subagents:
  │           acceptance criteria)       │  block Stop while red        adversarial / safety / verifier)
  └──────────── docs/progress.md, ADRs ◀─┴──────────────── evals for capability claims
```

| Practice | Where it lives | Source |
|---|---|---|
| Give the agent a check it can run; show evidence, not assertions | `scripts/check.sh`, PR template | Claude Code best practices ("Give Claude a way to verify its work") |
| Deterministic gates via hooks rather than advisory rules | `.claude/settings.json`, `.claude/hooks/` | Claude Code best practices ("Set up hooks"), hooks reference |
| Separate generator from evaluator; agree testable criteria up front ("sprint contract") | `ship` skill step 2 and 5, `adversarial-reviewer` | Anthropic engineering, "Harness design for long-running application development" |
| Reviewer reports only correctness/requirement gaps, with repro; avoid over-engineering from nit-chasing | `adversarial-reviewer.md` | Claude Code best practices ("Add an adversarial review step") |
| Progress file + git history + one feature at a time + clean state each session | `docs/progress.md`, `orchestrate` skill | Anthropic engineering, "Effective harnesses for long-running agents" |
| Subagents for wide reading and verification to protect the main context | `researcher`, `verifier`, `orchestrate` skill | Claude Code best practices ("Use subagents for investigation"), sub-agents docs |
| Short CLAUDE.md; on-demand detail in skills | `CLAUDE.md`, `.claude/skills/` | Claude Code best practices ("Write an effective CLAUDE.md") |
| Property-based tests as the practical formal-methods rung | `tests/test_properties.py`, `property-tests` skill | Standard practice (Hypothesis); escalate to stateful/TLA+ only with evidence |
| Evals judged by environment state, not agent self-report | `evals/`, `run-evals` skill | Mobile GUI-agent benchmarks (AndroidWorld-style state checks) |
| Human escalation only for consequential, non-obvious choices | `page-joon` skill, ADR 0006 | joon's operating instructions |

## Sources
- Claude Code best practices: https://code.claude.com/docs/en/best-practices
- Hooks reference: https://code.claude.com/docs/en/hooks
- Subagents: https://code.claude.com/docs/en/sub-agents
- Effective harnesses for long-running agents: https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
- Harness design for long-running application development: https://www.anthropic.com/engineering/harness-design-long-running-apps

## What's deliberately not here (yet)
- **Claude review in CI** (`anthropic/claude-code-action`): needs an `ANTHROPIC_API_KEY` repo secret and per-PR spend. In-session review covers it for now; add it if PRs start landing without review.
- **Formatter enforcement** (`ruff format --check`): would reformat most files at once and conflict with in-flight branches. Turn it on in a quiet moment with one formatting-only commit.
- **Device evals in CI**: needs an emulator with KVM and a GPU server; too costly per PR. Run on demand per `run-evals`.
