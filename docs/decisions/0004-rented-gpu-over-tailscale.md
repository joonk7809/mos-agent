# 0004. Serve the model on a rented GPU reached over Tailscale

- Status: Accepted
- Date: 2026-11-16
- Deciders: joon

## Context
joon doesn't want to buy hardware (no 3090/home server) until owning clearly pays off; the plan doc estimates break-even around 2,000 GPU-hours.

## Decision
`scripts/start_gpu_server.sh` brings up vLLM + the MOS server + userspace Tailscale (hostname `mos-gpu`) on any rented 24 GB+ GPU, with state on `$MOS_DATA`. Phones reach it only over the tailnet.

## Alternatives considered
- **Home GPU server** - capital cost before usage is known.
- **Public endpoint with TLS** - larger attack surface for an agent that controls phones.

## Consequences
Instances must be stopped when idle (cost). Latency depends on the provider region. Revisit when monthly GPU spend approaches the cost of owned hardware.
