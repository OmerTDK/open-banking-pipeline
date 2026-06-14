# open-banking-pipeline

Multi-bank PSD2-style ingestion into a canonical, categorized transaction schema with data contracts

> Status: under construction — not yet at definition-of-done.

Phase 0 done: canonical pydantic v2 schema with deterministic idempotency keys, fixture sets for three divergent mock banks (PSD2-style JSON, FDX-style JSON, legacy CSV), 71 passing tests, ADR-0001.

Phase 1 done: three in-process mock bank APIs (page-link, cursor, and whole-file-download shapes) with seeded 429/truncation fault injection, per-bank adapters into the canonical schema, retry with backoff, and an idempotent DuckDB landing store — `make ingest` lands all 46 fixture transactions, a second run lands zero duplicates; 175 passing tests, ADR-0003.

Phase 2 done: code-derived data contracts for 4 subjects (42 fields, primary keys, enum value sets, semantic notes) committed under `contracts/` with an append-only subjects ledger anchoring the baseline, a breaking-change detector classifying 15 change types with semver-ish bump enforcement plus a consumer manifest veto, gating every PR via `make contracts-check` in ~0.1 s; 295 passing tests, ADR-0004.

Phase 3 done: deterministic first-match categorization engine (3-group rule precedence: raw bank label > large-inflow salary heuristic > 68 description/counterparty keyword rules) covering all 13 non-UNCATEGORIZED categories; wired into the ingestion runner via `apply_category` so every landed transaction carries a category; schema-drift guard kills on uncovered enum values; spend-by-category-by-month consumption mart (`open-banking-mart`) proves the categorized canonical layer is consumable; `make e2e` now prints the full spend summary after the idempotency check; 2 consumer manifests registered; 359 passing tests, ADR-0005.

## Why this exists

Open Banking aggregation is the canonical EU fintech integration problem: N upstream providers with deliberately divergent schemas, pagination, and auth shapes, all forced into one governed canonical model. The hard parts are architectural, not mechanical — schema design under divergence, idempotent replay-safe loads, and machine-enforced data contracts. This pipeline demonstrates all three at staff analytics-engineering level with zero cloud credentials required.

## Architecture

```
fixtures/
  fjellvik/     PSD2-style JSON — nested booked/pending arrays, amounts as strings
  marlstone/    FDX-style JSON  — flat camelCase, unsigned amounts + DEBIT/CREDIT
  taktwerk/     Legacy CSV export — dd.mm.yyyy dates, decimal-comma amounts, no tx IDs

Mock bank APIs (in-process)
  FjellvikMockBank   page-link pagination, planned 429 injection
  MarlstoneMockBank  cursor pagination
  TaktwerkMockBank   whole-file download, planned truncation injection

Per-bank adapters          -->  CanonicalTransaction (pydantic v2, frozen)
  fjellvik.py                   idempotency key: SHA-256(bank+account+tx_id)
  marlstone.py                  taktwerk: content-derived key (no stable tx ID)
  taktwerk.py

Categorization engine      -->  apply_category() stamps every transaction
  categorization.py             before it lands; 3-group first-match rules

Landing store (DuckDB)     -->  idempotent, first-write-wins
  accounts + transactions       LandingConflictError on content disagreement
  LandingStore.open()           replay is a no-op; drift is loud

Data contracts (code-derived)
  canonical_account.json        4 subjects, 42 fields
  canonical_transaction.json    breaking-change detector in CI
  landing_accounts.json         consumer veto on pinned fields
  landing_transactions.json

Consumer manifests
  categorization_engine.json    pins 7 canonical_transaction fields
  spend_mart.json               pins 4 canonical_transaction fields

Spend mart
  build_spend_mart()            outflow spend by category by month
  open-banking-mart CLI         formatted table; wired into make e2e
```

## Results

All numbers from `make ci` on the fixture data (6 accounts, 46 transactions).

| Metric | Value |
|---|---|
| Test count (phase 3) | 359 tests, 0 failures |
| Tests added this phase | 64 (was 295 after phase 2) |
| `make ci` runtime | ~3 s end-to-end |
| Transactions landed | 46 (15 fjellvik + 16 marlstone + 15 taktwerk) |
| Second-run new rows | 0 (replay-safe) |
| Categories assigned | 12 of 14 categories reached on fixture data; UNCATEGORIZED present, TRANSFER present |
| Total fixture outflow spend | EUR 7 690.64 (May 2026) |
| Largest spend category | rent EUR 3 034.56 (3 transactions: fjellvik, marlstone, taktwerk) |
| Contract subjects | 4, each code-derived and CI-gated |
| Consumer manifests | 2 (categorization_engine, spend_mart) |
| Contract check runtime | ~0.1 s |

Spend summary from `make e2e` (46 fixture transactions, May 2026 outflows only):

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

## Design decisions

See [docs/adr/](docs/adr/) — each major decision documented with its trade-offs.

| ADR | Decision |
|---|---|
| [0001](docs/adr/0001-canonical-schema-and-mock-bank-strategy.md) | Canonical schema fields; three mock banks with divergent shapes; content-derived IDs for ID-less sources |
| [0003](docs/adr/0003-mock-api-shapes-and-ingestion-architecture.md) | Mock API shapes; ingestion architecture; idempotency and failure isolation |
| [0004](docs/adr/0004-data-contracts-and-breaking-change-detection.md) | Code-derived contracts; 15-type change classifier; consumer manifest veto |
| [0005](docs/adr/0005-categorization-and-spend-mart.md) | First-match rule engine; runner placement; Q8 raw-replay decision; mart grain |

## Quickstart

```bash
git clone https://github.com/OmerTDK/open-banking-pipeline
cd open-banking-pipeline
uv sync
make ci          # lint + 359 tests + contract check + e2e with spend mart
make ingest      # land all 46 fixture transactions with fault injection
make mart        # print spend summary from data/local/landing.duckdb
```

No cloud credentials, no external dependencies — everything runs from checked-in fixtures.

## Standards

Engineering conventions in [standards/](standards/) govern all code in this repo.
