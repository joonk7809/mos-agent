---
name: adr
description: Record an architecture or research decision as an ADR in docs/decisions/. Use when choosing between real alternatives (model, protocol, permission model, infra, data format, eval method), when reversing an earlier decision, or when joon answers a page.
---

# Write an ADR

1. Next number: `ls docs/decisions/ | grep -E '^[0-9]{4}-' | tail -1`, add one. File: `docs/decisions/NNNN-short-slug.md`.
2. Copy `docs/decisions/0000-template.md`. Keep it under one screen. The point is the *why* and the *rejected alternatives*, so a future agent doesn't silently undo it.
3. Status is one of: `Proposed` (waiting on joon, link the pager issue), `Accepted`, `Superseded by NNNN`, `Rejected`. Who decided goes in `Deciders`: "agent" for routine choices made autonomously, "joon" for anything he answered.
4. Never edit the Decision of an Accepted ADR. Write a new one that supersedes it and update the old one's Status line only.
5. Add a row to the index table in `docs/decisions/README.md`.
