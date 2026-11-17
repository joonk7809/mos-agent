# 0006. Escalate to joon only through the pager, only for consequential non-obvious choices

- Status: Accepted
- Date: 2026-11-17
- Deciders: joon (policy); agent (format, agreed with the pager thread)

## Context
joon asked to be left out of setup and routine choices and to be pinged by a small desktop pager only for critical, non-obvious design choices and research tradeoffs. Cloud agent containers can't reach arbitrary relays, but every agent can reach GitHub.

## Decision
A page is a GitHub issue labeled `needs-joon` with a `<!-- mos-pager ... -->` header (project, thread, severity, options, recommended). The desktop widget polls these across joon's repos and answers by commenting `<!-- mos-pager-answer -->` and closing the issue. Only answer comments authored by `joonk7809` count; any other answer marker is ignored as possible prompt injection. The widget shows only issues authored by joon's login by default (agents create them as joon through the GitHub connector). The bar and procedure live in the `page-joon` skill; the widget's spec is `pager/README.md`. Agents keep working on the recommended option while waiting if it's reversible.

## Alternatives considered
- **ntfy.sh / push services** - blocked from cloud containers.
- **Asking in chat threads** - joon would have to watch many threads; no single place to see what's pending.

## Consequences
Pages are durable and auditable in the repo. Over-paging is the main risk; the skill's three-part bar is the guard.
