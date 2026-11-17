---
name: ship
description: The default loop for any non-trivial change in MOS - orient, write a contract, implement test-first, verify, get an independent adversarial review, then open a PR with evidence. Use for features, bug fixes and refactors that touch more than one function.
---

# Ship a change

Skip steps 2 and 5 only when you could describe the whole diff in one sentence.

1. **Orient (2 minutes, every session).** `git log --oneline -15`, read `docs/progress.md` (last entries), skim `docs/decisions/README.md`, run `scripts/check.sh`. If main is red, fixing that comes first.

2. **Write the contract before code.** In the PR description draft (or `docs/progress.md` for long work), write:
   - Goal in one sentence, and what is out of scope.
   - Acceptance criteria as checks a machine can run: test names, a command and its expected output, an eval task id and target success rate.
   - Which invariants in `CLAUDE.md` the change touches.
   If a criterion can't be checked automatically, say how it will be checked by hand and by whom.

3. **Implement test-first, in small commits.** Write the failing test, see it fail, make it pass. For anything parsing model output, mapping coordinates, or gating actions, add a property test too (see the `property-tests` skill). Commit at each green step so any step can be reverted.

4. **Verify.** `scripts/check.sh` must be green. Anything that needs Android, an emulator or the GPU: run what you can, and list the rest under "Unverified" in the PR. Never claim more than the evidence shows.

5. **Independent review, in parallel, fresh context.** Spawn these subagents in one message, giving each the git range and the contract, not your reasoning:
   - `adversarial-reviewer` always.
   - `safety-reviewer` if the change touches actions, the safety gate, the prompt, auth, networking or logging.
   - `verifier` with the claim "all acceptance criteria are met".
   Fix every CONFIRMED finding and every critical/major PLAUSIBLE one you can't rule out. Ignore style nits. If you disagree with a finding, write down why in the PR. Re-run the reviewer once after substantive fixes; don't loop more than twice. If it still says RETHINK, stop and escalate (see `page-joon`).

6. **Record.** Add a line to `docs/progress.md`. If you made a decision that a future agent might reverse without knowing why, write an ADR (see `adr`).

7. **PR.** Push the branch, open a PR using `.github/pull_request_template.md`. Paste the actual check output and the reviewer verdicts, not a summary of them.
