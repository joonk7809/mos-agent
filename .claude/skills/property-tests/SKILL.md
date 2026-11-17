---
name: property-tests
description: Write Hypothesis property-based or stateful tests for MOS invariants - the lightweight formal-verification layer. Use when changing model-output parsing, coordinate mapping, the safety gate, the server session state machine, the wire protocol, or SFT export.
---

# Property tests (our formal-verification layer)

Verification ladder, cheapest first. Use the lowest rung that actually pins the invariant down:
1. **Types**: `mypy` (in `scripts/check.sh`). Make illegal states unrepresentable where cheap (dataclasses, `Literal` action types).
2. **Example tests**: the cases you thought of.
3. **Property tests** (Hypothesis): a stated invariant checked over generated inputs. This is the default for anything that touches model output, because model output is adversarial in practice.
4. **Stateful tests** (`hypothesis.stateful.RuleBasedStateMachine`): for the session lifecycle (create, step, reply, decline, terminate, max_steps, server restart). Model the protocol as a state machine and let Hypothesis find bad orderings.
5. **Model checking / proofs**: only if a bug class keeps escaping 3 and 4 (for example, a TLA+ spec of the phone<->server protocol). Write an ADR before investing here.

How to write one (see `tests/test_properties.py`):
- The docstring states the invariant in one sentence starting with "Invariant:". If you can't state it, you don't have a property yet.
- Generate the nasty inputs on purpose: NaN/inf, bools where numbers go, empty and huge lists, unicode, separators in numbers, boxes with inverted corners.
- When Hypothesis finds a counterexample, keep it: add it as a plain example test (or `@example(...)`) so it stays covered even if generation changes, then fix the code.
- Keep each property fast (default 100 examples). Use `@settings(max_examples=...)` to go higher only for the safety gate.

Invariants worth protecting (extend this list as you add them):
- Any model output yields either a ParseError or an on-screen action. Never a crash, never an off-screen tap.
- A tap anywhere inside a send/pay/delete-like element requires confirmation.
- Card-number-like or password-field typing requires confirmation.
- `format_response` then `parse_response` round-trips (history replay is faithful).
- A terminated or errored session never returns another action.
