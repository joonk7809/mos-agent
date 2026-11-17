# 0002. Use open-weight MAI-UI-8B as the phone-control model

- Status: Accepted
- Date: 2026-11-16
- Deciders: joon

## Context
joon wants a phone with an agent built in, cheap to run, for himself and friends, and wants to own the model (fine-tune and post-train it). Claude computer use was the first prototype (`mos/agent.py`).

## Decision
We serve MAI-UI-8B (Tongyi-MAI, Apache-2.0) with vLLM and use its native prompt/action format (`mos/maiui.py`). The Claude agent stays only as a baseline for evals.

## Alternatives considered
- **Claude computer use** - not ownable or fine-tunable; per-call cost.
- **Larger open models** - need more than one 24 GB GPU; revisit if 8B can't reach acceptable success after QLoRA.

## Consequences
Prompting must match the official agent exactly. Improving the model means collecting demonstrations (`mos/export_sft.py`) and QLoRA. Revisit if a clearly better open mobile-agent model appears at similar size.
