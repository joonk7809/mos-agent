# 0001. Record decisions as ADRs in the repo

- Status: Accepted
- Date: 2026-11-17
- Deciders: agent (agentic dev workflow setup)

## Context
Most work on MOS is done by agents across many sessions and threads that don't share context. Without a written why, a later agent will "fix" a deliberate choice (e.g. reintroduce Shizuku, or swap the model) and redo settled research.

## Decision
We keep lightweight Architecture Decision Records (Nygard format) in `docs/decisions/`, one file per decision, indexed in `README.md`. Agents write them for decisions with real alternatives; joon's answers to pages are recorded as Accepted ADRs.

## Alternatives considered
- **Project memory only** - not visible from the repo, not reviewable in PRs.
- **One big DECISIONS.md** - grows into something agents skim past; per-file ADRs are greppable and supersedable.

## Consequences
A little writing per decision. `CLAUDE.md` points here, so agents check before re-deciding.
