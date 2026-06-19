# ADR-0006: End-to-end validation strategy and definition-of-done

**Date:** 2026-06-14
**Status:** Accepted

## Context

Phase 5 closes out the pipeline toward the brief's definition-of-done. Three
things needed deciding:

1. **How to validate the full pipeline in CI** — the brief requires a
   demonstrated end-to-end run, not just unit tests. The question is where to
   draw the line between `make e2e` (a shell script that counts grep matches)
   and a proper pytest integration test suite.
2. **Kill-verification discipline** — the brief requires that at least one key
   invariant be kill-verified: apply a one-line mutant, confirm the specific
   test fails, revert. Which invariant to pick, and whether to document the
   procedure in code or only in the ADR.
3. **Subjects ledger as the hardest design decision** — ADR-0004 described the
   subjects ledger briefly. Phase 5 is the right moment to document *why* it
   is the hardest part of the contracts design, what the trade-off actually is,
   and what the remaining gap is.

## Decisions

### 1. Two-layer e2e validation

`make e2e` (the Makefile target) and `tests/test_e2e_pipeline.py` (pytest) are
complementary, not redundant.

`make e2e` is a shell-level smoke test: it runs the CLI binaries, counts grep
matches in their stdout, and exits non-zero on mismatch. It validates that the
installed entry points work end-to-end and that the zero-new-rows invariant
holds at the CLI surface — the output format being tested is the contract with
any future scheduler.

`tests/test_e2e_pipeline.py` is a programmatic integration test: it imports
the Python API directly, tests invariants with precise assertions, and makes
kill-verification straightforward. The key tests in `TestIdempotencyInvariant`
and `TestContractGate` document not just what must be true but exactly which
code path each test exercises and what breaks if that path is wrong.

Both run in CI; `make ci` runs `make e2e` last so a green unit+contract suite
but a broken CLI surface is still a CI failure.

### 2. Kill-verified invariant: conflict detection in _insert_atomically

The invariant chosen for kill-verification is the `existing != record` branch
in `LandingStore._insert_atomically` — the line that distinguishes a harmless
replay (same id, identical content) from a dangerous upstream correction (same
id, different content).

**Why this invariant?** First-write-wins idempotency is the pipeline's central
reliability claim. If a replay raised `LandingConflictError`, no pipeline run
would ever complete twice against the same store. If an upstream correction
were silently accepted, the canonical layer would have undocumented overwrite
semantics and audit would be unreliable. The two paths are logically exclusive
and both production-critical; the mutant (`existing == record`) swaps them.

**Kill-verify result:**
- Mutant applied: `elif existing != record:` → `elif existing == record:`
- `test_replay_is_always_a_no_op`: FAILED (replay now raises `LandingConflictError`)
- `test_conflict_detection_kills_on_content_change`: FAILED (amended record accepted silently)
- `test_seeded_fault_injection_produces_same_landing_data`: PASSED
- Mutant reverted: all 383 tests green.

The two failing tests are exactly the two tests that document the invariant —
the kill proves the tests are testing the right thing, not just asserting
incidentally-true facts.

### 3. The subjects ledger: the hardest design decision

The hardest design decision in this pipeline is not the canonical schema or the
adapter-per-bank architecture — both have obvious shapes once you accept the
requirements. The hardest decision is the subjects ledger in the contracts
subsystem, specifically the choice of *what counts as the baseline* for
breaking-change detection.

**The problem.** The contracts check compares the currently-generated contract
(from code) against the committed artifact. But what anchors the committed
artifact? If the artifact is the baseline, then:

- Deleting an artifact and re-running `generate` silently resets the baseline
  to current code — a breaking change ships with no bump, CI green.
- Hand-editing an artifact's fields while keeping its version produces a
  falsified history — the tool sees no change and reports OK.

Both attacks require a malicious (or careless) developer action, but neither
requires a git revert or a visible version change. The committed artifacts alone
are a forgeable baseline.

**The candidates.**

| Option | Description | Flaw |
|---|---|---|
| Committed artifacts only | Diff code against the artifact; fail on unbumped changes | Forgeable: delete + regenerate resets history |
| Git diff against merge-base | Regenerate from code, diff against the base-branch artifact | Correct for content; requires git state (fetch depth, merge-base) inside the tool; cassette fixtures for tests |
| Subjects ledger | Append-only map of subject → last recorded version, committed alongside artifacts | Pins version floor; deleting or rewinding an artifact is a hard failure; hand-editing requires touching two files in one diff |

**The decision.** The subjects ledger is the interim answer — not the complete
one. It closes the delete-and-regenerate loophole: a recorded subject whose
artifact is missing is a hard failure; `generate` refuses to recreate it
(restoring from git is the only path forward). It also closes the
rewind-the-version loophole: a committed artifact whose version is behind its
recorded version fails immediately. Forging continuity now requires editing
the ledger *and* the artifact in the same change set — exactly the kind of
two-file diff a reviewer cannot miss.

**What the ledger does not close.** Someone who hand-edits an artifact's
*field list* (not just its version) while keeping both the artifact version
and the ledger entry unchanged can still fake content continuity. The tool
will see no diff between the committed artifact and code — because the
hand-edit made them agree — and report clean. This is visible in a code
review diff but invisible to the automated tool. The git-baseline approach
(diffing against the merge-base artifact from git history) closes this; the
ledger does not.

**Why accept the gap?** The git-baseline approach has its own costs: it
makes the detector depend on git state inside the running process (fetch
depth, merge-base resolution, branch-specific history), and tests require
repo fixtures or a git mock. The ledger gives 90% of the protection with
none of those costs. The residual loophole requires deliberate two-file
hand-editing, which is reviewer-visible even if tool-invisible. For a
portfolio pipeline where the only actor is the author, the ledger is enough;
for a team pipeline with adversarial incentives, the git-baseline extension
is the right next step.

**Why this is harder than the canonical schema.** The canonical schema has a
clear design point: one canonical representation, adapters absorb divergence,
consumers are shielded. The subjects ledger has no such clean design point.
Every choice trades one class of forgery for another, and the right answer
depends on the trust model of the team and the tooling cost budget. The schema
design is an architecture call; the ledger is a security design call. Security
design calls are harder because the failure mode is silent and the threat model
is not always obvious.

## Alternatives considered

- **E2e tests only in Makefile, no pytest integration tests** — rejected: shell
  grep-count assertions are hard to extend, impossible to kill-verify precisely,
  and provide no programmatic API for future test composition.
- **Kill-verify the idempotency key derivation instead** — considered: a mutant
  in `derive_transaction_id` (e.g. dropping `source_bank` from the key material)
  would cause cross-bank ID collisions. Rejected as the kill-verify target for
  this phase because the collision only manifests when two different banks share
  a source transaction id, which the current fixture data does not exercise.
  The derivation tests in `test_canonical_models.py` cover the formula; the
  idempotency invariant covers the store contract.
- **Full git-baseline breaking-change detection** — deferred (see above). The
  ledger is the interim answer; the git-baseline extension remains the natural
  completion if content-level continuity ever needs enforcing without relying
  on reviewer attention.

## Consequences

- The e2e test suite now documents the pipeline's key invariants in code, not
  just in a README. A future author can read `test_conflict_detection_kills_on_content_change`
  and understand precisely what the idempotency contract guarantees and how to
  break it.
- The subjects ledger is now documented as a deliberate interim answer, not a
  complete solution. A future ADR amendment or ADR-0007 can specify the git-
  baseline extension if the trust model demands it.
- Kill-verification is now a first-class practice in this codebase: the test
  file documents the mutant, the expected kills, and the revert confirmation.
  Future test authors have a pattern to follow.
- 383 tests, 0 failures; `make ci` green in ~3 s.
