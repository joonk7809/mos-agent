# Decision log

Architecture and research decisions, one file each ([why](0001-record-decisions-as-adrs.md)). Use the `adr` skill to add one; copy [0000-template.md](0000-template.md).

| # | Decision | Status | Deciders |
|---|---|---|---|
| [0001](0001-record-decisions-as-adrs.md) | Record decisions as ADRs in the repo | Accepted | agent |
| [0002](0002-open-weight-mai-ui-8b.md) | Use open-weight MAI-UI-8B as the phone-control model | Accepted | joon |
| [0003](0003-accessibility-only-android-app.md) | The phone app uses only accessibility APIs | Accepted | joon |
| [0004](0004-rented-gpu-over-tailscale.md) | Serve the model on a rented GPU over Tailscale | Accepted | joon |
| [0005](0005-verification-gates-and-independent-review.md) | Gate changes on checks plus independent agent review | Accepted | agent |
| [0006](0006-escalate-to-joon-via-pager.md) | Escalate to joon only via the pager | Accepted | joon / agent |
| [0007](0007-ab-gui-owl-before-fine-tuning.md) | A/B GUI-Owl-1.5-8B against MAI-UI-8B before fine-tuning | Accepted | joon |
