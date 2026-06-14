# open-banking-pipeline

Multi-bank PSD2-style transaction aggregation pipeline with a canonical schema,
deterministic categorization, and machine-enforced data contracts.

---

## What this demonstrates

Open Banking aggregation is the canonical EU fintech integration problem: N
upstream providers with deliberately divergent schemas, pagination shapes, and
error behaviors, all forced into one governed canonical model. The hard parts
are architectural:

- **Canonical schema design under divergence** — every mapping decision is
  documented (ADR-0001) and tested; the schema is strict enough that drift
  surfaces as loud validation errors.
- **Idempotent, replay-safe loads** — the same pipeline run produces identical
  state whether it is the first run or the hundredth; upstream corrections
  raise a loud error, never a silent overwrite.
- **Machine-enforced data contracts** — breaking changes to the canonical schema
  are classified and blocked in CI before any consumer is affected; a
  demonstrated caught break is below.

No cloud credentials, no live endpoints, no external services. Clone and run.

---

## Architecture

```
fixtures/
  fjellvik/      PSD2-style JSON — nested booked/pending arrays,
                 amounts as strings in a transactionAmount object
  marlstone/     FDX-style JSON — flat camelCase, unsigned amounts
                 plus DEBIT/CREDIT indicator
  taktwerk/      Legacy CSV export — dd.mm.yyyy dates, decimal-comma
                 amounts, no transaction ID column

Mock bank APIs (in-process, no HTTP)
  FjellvikMockBank   page-link pagination  + planned 429 injection
  MarlstoneMockBank  cursor pagination
  TaktwerkMockBank   whole-file download   + planned truncation injection

Per-bank adapters
  fjellvik.py    PSD2 nested JSON  ->  CanonicalTransaction
  marlstone.py   FDX flat JSON     ->  CanonicalTransaction
  taktwerk.py    legacy CSV        ->  CanonicalTransaction
                 (content-derived idempotency key — no stable tx ID)

Categorization engine
  apply_category()    stamps every transaction before it lands
  3-group first-match: raw bank label > salary heuristic > 68 keywords

Landing store (DuckDB, single file)
  accounts + transactions
  first-write-wins idempotency: replay = no-op, content change = loud error
  LandingConflictError on any content disagreement

Data contracts (code-derived, 4 subjects, 42 fields)
  canonical_account.json        pydantic model -> JSON artifact
  canonical_transaction.json    15 change types classified; semver bump enforced
  landing_accounts.json         DDL specs -> JSON artifact
  landing_transactions.json

Consumer manifests (2)
  categorization_engine.json    pins 7 canonical_transaction fields
  spend_mart.json               pins 4 canonical_transaction fields

Spend mart
  build_spend_mart()   outflow spend by (year, month, category)
  open-banking-mart    formatted table CLI
```

---

## Quickstart

```bash
git clone https://github.com/OmerTDK/open-banking-pipeline
cd open-banking-pipeline
uv sync

make ci          # lint + 383 tests + contract check + e2e (~3 s)
make ingest      # land 46 fixture transactions into data/local/landing.duckdb
make mart        # print spend summary from the local store
```

---

## The demonstrated caught break

The brief requires a demonstrated breaking change caught by CI. Here it is.

**Scenario:** a bank changes the `amount` field type from `decimal` to `string`
in its schema. Without a contract gate, this reaches every consumer silently.

**What CI sees** (simulated by editing `contracts/canonical_transaction.json`
and running `make contracts-check`):

```
canonical_transaction.amount [breaking] type_changed:
    type changed from 'string' to 'decimal'
PROBLEM: canonical_transaction: breaking changes require a major bump:
    1.0.0 -> 1.0.0 is not a major increase
PROBLEM: canonical_transaction: committed artifact does not match
    the code-derived contract; run `make contracts-generate`
contracts check: FAILED
```

Exit code 1. PR cannot merge.

**What fixing it looks like:**

1. Bump `canonical_transaction` to `2.0.0` in `src/.../contracts/versions.py`.
2. Update `contracts/consumers/categorization_engine.json` and
   `contracts/consumers/spend_mart.json` — both pin `amount` and must
   acknowledge the new version, forcing a coordinated change set.
3. Run `make contracts-generate` to regenerate the artifact.
4. `make ci` passes.

The consumer manifests are the key mechanism: a breaking change to a pinned
field requires the consumer to acknowledge it in the same change set. Producer
and consumer move together or CI holds.

Tests `TestContractGate::test_type_change_on_amount_is_a_breaking_change` and
`test_removing_amount_field_from_contract_is_a_breaking_change` in
`tests/test_e2e_pipeline.py` automate this scenario as part of the test suite.

---

## Kill-verified invariant

The central reliability claim of the pipeline is first-write-wins idempotency:
running ingestion twice produces zero new rows on the second run. The invariant
lives in the `existing != record` branch of `LandingStore._insert_atomically`.

**Kill-verify result (recorded in ADR-0006):**

Mutant applied: `elif existing != record:` → `elif existing == record:`

| Test | Mutant result |
|---|---|
| `test_replay_is_always_a_no_op` | FAILED — replay raised `LandingConflictError` |
| `test_conflict_detection_kills_on_content_change` | FAILED — amended record accepted silently |
| `test_seeded_fault_injection_produces_same_landing_data` | PASSED (unrelated path) |

Mutant reverted → 383 passed, 0 failures.

The kill proves the two tests target the right code path, not incidentally-true
facts.

---

## Results

All numbers from `make ci` on the fixture data (no cloud, no mocks beyond
checked-in fixtures).

| Metric | Value |
|---|---|
| Test count | 383 tests, 0 failures |
| Tests added this phase | 11 (was 372 after phase 3) |
| `make ci` runtime | ~3 s |
| Banks | 3 (fjellvik, marlstone, taktwerk) |
| Accounts | 6 (2 per bank) |
| Transactions | 46 total (15 + 16 + 15) |
| Second-run new rows | 0 (replay-safe) |
| Categories assigned | 12 of 14 categories reached; UNCATEGORIZED present, TRANSFER present |
| Total fixture outflow spend | EUR 7 690.64 (May 2026) |
| Largest spend category | rent EUR 3 034.56 (3 transactions across all banks) |
| Contract subjects | 4, code-derived, CI-gated |
| Contract fields | 42 across the 4 subjects |
| Consumer manifests | 2 (categorization_engine, spend_mart) |
| Change types classified | 15 (field removed, type changed, nullability, enum, etc.) |
| Contract check runtime | ~0.1 s |

Spend summary from `make e2e` (46 fixture transactions, May 2026 outflows):

```
Month      Category              Spend (EUR)   Txns
---------------------------------------------------
May 2026   rent                      3034.56      3
May 2026   travel                    2699.27      3
May 2026   transfer                   750.00      3
May 2026   cash_withdrawal            450.00      3
May 2026   utilities                  211.83      3
May 2026   entertainment              195.25      4
May 2026   dining                     110.45      3
May 2026   groceries                   96.53      3
May 2026   transport                   86.00      1
May 2026   shopping                    27.60      1
May 2026   healthcare                  18.35      1
May 2026   bank_fees                   10.80      2
---------------------------------------------------
Total                                 7690.64
```

---

## The hardest design decision

The hardest design decision is the subjects ledger in the contracts subsystem —
not the canonical schema, which has an obvious shape once you accept the
requirements.

**The problem:** What anchors the committed contract artifact as a baseline?
If the artifact alone is the baseline, deleting it and re-running
`make contracts-generate` silently resets history to current code — a breaking
change ships with no version bump and CI stays green.

**The candidates:**

| Option | Closes the delete-and-regenerate hole | Cost |
|---|---|---|
| Committed artifacts only | No | None |
| Git diff against merge-base | Yes | Requires git state inside the tool; test fixtures need a real git repo |
| Subjects ledger (chosen) | Yes | One extra committed file; append-only |

**The choice and its gap:** `contracts/_subjects_ledger.json` is an append-only
map of subject → last recorded version. Deleting an artifact is a hard failure
(the ledger records it existed). Rewinding a version is a hard failure (the
artifact is behind the ledger floor). Forging continuity now requires editing
the ledger and the artifact in the same change set — a two-file diff a reviewer
cannot miss.

What the ledger does not close: someone who hand-edits an artifact's field list
while keeping the version (and the ledger entry) unchanged fools the tool,
because the committed artifact and the code-derived one now agree. This is
visible in the PR diff but invisible to automation.

The git-baseline approach closes that gap but makes the detector depend on git
state and requires cassette-style test fixtures. The ledger gives ~90% of the
protection at near-zero cost. ADR-0006 documents the trade-off and the
extension path. ADR-0004 documented the original design.

---

## Design decisions (ADR index)

| ADR | Decision |
|---|---|
| [0001](docs/adr/0001-canonical-schema-and-mock-bank-strategy.md) | Canonical schema fields; three mock banks with divergent shapes; content-derived IDs for ID-less sources |
| [0003](docs/adr/0003-mock-api-shapes-and-ingestion-architecture.md) | Mock API interaction shapes; ingestion architecture; idempotency and failure isolation |
| [0004](docs/adr/0004-data-contracts-and-breaking-change-detection.md) | Code-derived contracts; 15-type change classifier; consumer manifest veto; subjects ledger |
| [0005](docs/adr/0005-categorization-and-spend-mart.md) | First-match rule engine (3 groups, 68 keywords); runner placement; Q8 raw-replay decision; mart grain |
| [0006](docs/adr/0006-e2e-validation-and-definition-of-done.md) | Two-layer e2e validation; kill-verified invariant; subjects ledger as the hardest decision |

---

## Definition-of-done status

| Item | Status |
|---|---|
| README with system story and architecture diagram | **done** |
| ADRs for each major decision (tradeoff documented) | **done** — ADR-0001, 0003, 0004, 0005, 0006 |
| Full CI green — lint + tests on every PR | **done** — `make ci` ~3 s, 0 failures |
| Meaningful tests beyond not_null/unique | **done** — conflict detection, category wiring, idempotency, reproducibility, contract gate, kill-verified invariant |
| Observability (test results, freshness, anomalies) | **done** — `make e2e` prints per-bank counts + zero-new-rows check + spend mart; CI enforces on every PR |
| Results section with quantified outcomes | **done** — 383 tests, 46 txns, EUR 7 690.64, ~3 s CI |
| Generated docs published | **partial** — ADRs and README are the docs; a Sphinx/MkDocs HTML build is a future extension |
| Short writeup of the hardest design decision | **done** — subjects ledger, above and in ADR-0006 |
| Conforms to Omer's coding standards | **done** — ruff, uv, TDD, explicit columns, no SELECT *, type hints |
| Public repo with clean history | **pending** — Omer flips visibility when ready |

Open questions deferred by design:
- **Q7** (upstream corrections versioning) — first-write-wins raises
  `LandingConflictError` on content change; the correction strategy (SCD2
  append, reject-and-alert, or version column) is a future ADR.
- **Q9** (git-baseline diffing) — the subjects ledger is the interim answer;
  ADR-0006 documents the extension path.

---

## Standards

Engineering conventions in [standards/](standards/) govern all code in this repo.

| Standard | Link |
|---|---|
| Engineering principles | [standards/engineering-principles.md](standards/engineering-principles.md) |
| Python standards | [standards/python-standards.md](standards/python-standards.md) |
| SQL standards | [standards/clean-sql.md](standards/clean-sql.md) |
| dbt standards | [standards/dbt-standards.md](standards/dbt-standards.md) |
| Git workflow | [standards/git-workflow.md](standards/git-workflow.md) |
