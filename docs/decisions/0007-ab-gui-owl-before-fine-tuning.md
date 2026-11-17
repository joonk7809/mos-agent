# 0007. A/B GUI-Owl-1.5-8B against MAI-UI-8B before fine-tuning

- Status: Accepted
- Date: 2026-11-17
- Deciders: joon (decision card in the project thread), agent (PR #3)

## Context
ADR 0002 picked MAI-UI-8B. A 2026-11 survey (project file `research/mobile-agent-landscape-2026-11.md`) found three 8B open models within ~3 points on AndroidWorld (saturated, all self-reported), but on MobileWorld, which MAI-UI's own lab runs, GUI-Owl-1.5-8B-Instruct (MIT) reports 38.2 vs MAI-UI-8B's 27.5 on GUI-only tasks and 53.8 vs 22.2 on ask-user tasks. The QLoRA data we record is in one model's prompt/action format, so the model should be settled before collecting demos.

## Decision
We will support both models behind `MOS_POLICY` (`maiui` default, `guiowl`), run the same device-state evals on joon's emulator with each (`--repeats 3`), compare with `evals.compare` (paired per-task bootstrap), and fine-tune whichever wins. If the result is "too close to call", MAI-UI-8B stays (ADR 0002).

## Alternatives considered
- **Stay on MAI-UI-8B** - no GPU spend now, but risks building training data for the weaker model.
- **Switch to GUI-Owl now** - based only on self-reported numbers.

## Consequences
Two prompt formats to maintain until the A/B ends; `export_sft --policy` keeps their data apart. Costs a few rented GPU hours. Revisit if Qwen-UI-Agent releases open weights.
