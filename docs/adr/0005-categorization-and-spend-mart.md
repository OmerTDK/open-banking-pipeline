# ADR-0005: Deterministic categorization and spend-mart design

**Date:** 2026-06-14
**Status:** Accepted

## Context

Phase 3 adds transaction categorization — the first declared consumer of the
canonical transaction schema — plus a small downstream consumption mart that
proves the canonical layer is queryable.  Three decisions needed making before
writing code:

1. **Rule structure:** how to express categorization rules in a way that is
   testable, reproducible, and order-deterministic.
2. **Categorization placement:** where in the pipeline does the category get
   assigned — at the adapter, in the runner, or as a post-processing step?
3. **Q8 (open question from ADR-0004):** does categorization require a raw
   replay landing zone in this phase?

## Decisions

### 1. First-match, three-group rule engine

Rules are evaluated in three groups, top-wins:

| Group | Signal | Rationale |
|---|---|---|
| 1 | `raw_category` — bank's own label, normalised to lower | Highest precision: when a bank provides its own label, trust it over heuristics. |
| 2 | Amount threshold (>= 1 000 EUR positive) → SALARY | A large positive inflow is almost always salary; this catches cases where the bank gives no category and the description has no keyword. |
| 3 | Keyword substring search over `description` + `counterparty_name` | Covers the rest; 68 keywords across 13 categories. |

Within group 3, rules are evaluated in the order they appear in `_KEYWORD_RULES`.
This makes the engine fully deterministic: given the same inputs, the same
category always results.  Precedence conflicts between keyword rules (e.g. a
transaction description that contains both "cafe" and "shop") are resolved by
the tuple order — a design choice that must be documented but not abstracted.

**Trade-off accepted:** keyword rules have false-positives (a "bus ticket"
counterparty named "GREENFIELD SUPERMARKET BUS TERMINAL" would land as
GROCERIES before TRANSPORT).  The alternative — MCC-code-based rules or
merchant-name lookup — requires enrichment data the canonical schema does not
carry.  UNCATEGORIZED is the documented fallback for anything that falls
through; the schema-drift test `test_all_non_uncategorized_categories_are_reachable`
ensures every enum value is at least reachable, giving future authors a clear
extension contract.

**ML extension path:** a scoring model can replace or supplement group 3 by
consuming the same three inputs (`amount`, `description`, `counterparty_name`,
`raw_category`).  The `categorize()` signature is stable; swapping the
implementation requires no change at the call site.

### 2. Categorization runs in the runner, not the adapter

The category is assigned in `run_ingestion` (the runner), not in each bank
adapter, via `apply_category(transaction)` before the transaction is landed.

Three options were considered:

| Option | Pro | Con |
|---|---|---|
| Adapter assigns category | Category is set before any other code sees the transaction | Adapters become cross-cutting; they now know about the categorization layer, which belongs downstream |
| Runner assigns category (chosen) | Clean separation: adapters own parsing; the runner owns enrichment | One more step in `run_ingestion`; trivial |
| Post-process the store after landing | Avoids touching `run_ingestion` | Requires an UPDATE or re-insert against the DuckDB store; adds a write path; contradicts first-write-wins idempotency |

The runner option keeps adapters focused on parsing and the enrichment step
co-located with the load.  `apply_category` uses `model_copy` (pydantic v2)
to produce a new frozen instance with the category field set — no mutation.
`apply_category` is always authoritative: it re-runs categorization regardless
of the input's current category value.

### 3. Q8 — no raw replay landing zone needed in Phase 3

ADR-0004 noted Q8 as open: "categorization operates on canonical rows — decide
whether a raw replay landing zone is needed for this phase."

Decision: **no raw replay zone in Phase 3.**

The categorizer reads the three canonical fields (`amount`, `description`,
`counterparty_name`, `raw_category`); all three are present in the landed
canonical transactions table.  Re-categorization — correcting rules,
re-processing after a rule change — can be done by replaying the canonical
table itself, not the raw source.  The canonical table is append-only with
first-write-wins idempotency; a re-categorization run requires clearing the
table and re-ingesting from fixtures, which is already possible via a
`make e2e`-style fresh run.

A raw landing zone becomes necessary only if:
- A future enrichment pass needs fields dropped by the adapter (e.g. raw JSON
  fields not mapped into canonical), or
- The canonical schema changes in a way that invalidates historical rows and
  a source-faithful replay is needed.

This phase does not trigger either condition.  If a future phase does, ADR-0001
documents the raw zone as the planned home for such data; adding it is an ADR
amendment, not a breaking change to the canonical schema.

### 4. Spend mart grain, placement, and consumer manifest

**Grain:** one row per `(year, month, TransactionCategory)` — enough to answer
"how much did I spend on groceries in May 2026?" without a BI tool.

**Placement:** `mart.py` queries the DuckDB `transactions` table directly
through the `LandingStore._connection` handle.  This keeps the mart logic
co-located with the storage layer without adding an abstraction layer; the
store is the only writer and the only reader needs are one SQL query.

**Inflow exclusion:** rows where `amount >= 0` are excluded.  Salary and
refunds are not "spend".  A separate inflow mart (income vs. refund breakdown)
is a documented future extension.

**Ordering:** `(year, month ASC, total_spend DESC)` — chronological primary
order, with the highest-spend categories first within a month, which is the
most useful default for a spending summary.

**Consumer manifest:** `contracts/consumers/spend_mart.json` registers the mart
as a consumer of `canonical_transaction`, pinning four fields:
`transaction_id`, `booking_date`, `amount`, `category`.  A breaking change to
any of these now triggers the consumer-veto check in CI, coordinating producer
and consumer in the same change set (ADR-0004).

## Consequences

- Every transaction in the landing store carries a non-null, rule-determined
  category.  UNCATEGORIZED is a valid output, not a missing value.
- Adding a new `TransactionCategory` enum value without a corresponding rule
  fails `test_all_non_uncategorized_categories_are_reachable` in CI.
- Rule changes are backward-incompatible with stored categories.  Because the
  canonical table is the replay source, a rule change requires a fresh run to
  apply uniformly.  The ADR-0004 contracts framework does not capture rule
  version in the contract artifact — this is deliberate, as rule logic is not
  part of the canonical schema.
- The spend mart is not materialized; it is computed on every call.  At fixture
  scale (46 transactions) this is instant.  Materializing to a separate DuckDB
  table is the natural next step if query time becomes a concern.
- The `booking_date IS NOT NULL` filter in the mart query silently excludes
  pending transactions.  This is correct (pending amounts are not yet settled)
  but must be understood by consumers of the mart output.
