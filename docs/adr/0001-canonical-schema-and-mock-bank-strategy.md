# ADR-0001: Canonical transaction schema and mock-bank fixture strategy

**Date:** 2026-06-10
**Status:** Accepted

> **Amendment (2026-06-10):** This ADR says FX conversion detail "stays in the raw landing
> zone". ADR-0003 subsequently decided that Phase 1 persists only canonical-shaped data — no
> raw landing zone exists yet, so the per-bank FX detail currently lives only in the
> checked-in fixtures. If a consumer needs raw replay, a raw zone is added per ADR-0003's
> consequences. The wording below is left as written.

## Context

The pipeline must ingest transactions from multiple upstream banks whose APIs disagree on
everything: field names, nesting, amount representation, date formats, and status semantics.
The whole system must be reproducible by anyone who clones the repo — no real bank
credentials, no live endpoints. Phase 0 has to fix two things before any connector exists:
the shape of the one canonical schema everything converges on, and the strategy for the mock
banks that force real canonicalization work.

## Decision

### Three mock banks with deliberately divergent shapes

| Bank | Style | Divergence it forces |
| --- | --- | --- |
| `fjellvik` | Berlin-Group/PSD2-style JSON | Nested `booked`/`pending` arrays, amounts as **strings** inside a `transactionAmount` object, ISO dates, per-account endpoints, `currencyExchange` detail for FX |
| `marlstone` | FDX-style JSON | Flat camelCase entries, **unsigned numeric** amounts with a `DEBIT`/`CREDIT` indicator, ISO-8601 UTC timestamps, `POSTED`/`PENDING` status, `originalCurrency`/`originalAmount` for FX |
| `taktwerk` | Legacy CSV export | Semicolon delimiter, `dd.mm.yyyy` dates, decimal-comma amounts with dot thousands separators, no status column (booked-only), no transaction ID column, localized references, `Original Amount`/`Original Currency` columns for FX |

The bank names are invented; `taktwerk` is also the name of an unrelated Swiss consultancy,
a collision we accept (non-financial industry, generic German word).

Every bank books its amounts in the account currency, as real bank statements do; the
original foreign amount and currency of an FX transaction appear only as source-side detail
(fjellvik `currencyExchange`, marlstone `originalCurrency`/`originalAmount`, taktwerk
`Original Amount`/`Original Currency`). That keeps the canonical `amount` contract — signed,
in the account currency — satisfiable by every adapter.

All three are served from checked-in fixtures under `fixtures/<bank>/` containing 2 accounts
each and exactly 15 (fjellvik), 16 (marlstone) and 15 (taktwerk) transactions, including the
edge cases every adapter must survive: refunds
(positive inflows from merchants), foreign-currency transactions (represented differently per
bank), and pending-vs-booked (where the source supports it). Fixture-integrity tests pin
these properties so the edge cases cannot silently rot.

### Adapter-per-bank into one canonical schema

Each bank gets its own adapter (Phase 2/3) that maps the raw shape into one canonical schema
(`open_banking_pipeline.canonical`). There is no shared "generic mapper" — divergence lives
in the adapters, the canonical layer knows nothing about any bank's quirks.

### Canonical schema field decisions

`CanonicalTransaction` (pydantic v2, `frozen=True`, `extra="forbid"`):

- `transaction_id` — idempotency key: SHA-256 hex of `source_bank`, `source_account_id`,
  `source_transaction_id` joined by ASCII unit separator (`\x1f`). The join is injective
  because source identifiers are validated — in the derivation functions and at the model
  layer — to contain no control characters (the separator included); without that check an
  identifier containing the separator could shift material between fields and collide with a
  different record. Deterministic, so replayed loads are no-ops.
- `account_id` — `{source_bank}:{source_account_id}`; same derivation on
  `CanonicalAccount`, so the FK linkage cannot drift.
- `source_bank` / `source_account_id` / `source_transaction_id` — full lineage back to the
  raw record.
- `status` — two-value enum `booked` / `pending`; a model validator requires
  `booking_date` for booked rows.
- `booking_date` / `value_date` — plain dates; intraday timestamps are not part of the
  canonical contract because only one of three banks reliably provides them.
- `amount` — signed `Decimal` in the account currency (negative = outflow); zero is
  rejected as an upstream parsing bug. Adapters resolve each bank's sign convention
  (marlstone's unsigned-amount-plus-`DEBIT`/`CREDIT` included).
- `currency` — ISO 4217 pattern-validated (`^[A-Z]{3}$`).
- `counterparty_name` / `counterparty_account` — optional; legacy exports may lack them.
- `description` — optional free-text remittance information.
- `raw_category` — the bank's category verbatim (lineage for the categorizer);
  `category` — normalized enum, defaults to `uncategorized` until Phase 3 assigns it.

Both derived identifiers are **regular fields, recomputed and enforced by model
validators** rather than pydantic computed fields: computed fields are emitted by
`model_dump` but rejected on re-validation under `extra="forbid"`, which would break
serialize/re-validate round trips (verified against pydantic 2.13.4). Explicit fields keep
the contract round-trippable while making a wrong key impossible to construct.

### Idempotency for sources without transaction IDs (taktwerk)

taktwerk's legacy CSV export has no transaction ID column, so "identity is a pure function
of the source-provided ID" cannot apply to it. For ID-less sources the adapter derives
`source_transaction_id` from record content via `derive_content_source_transaction_id`:

- **Key material** — every column of the export row in header order, raw values exactly as
  exported (empty strings included), joined by the `\x1f` separator. For taktwerk that is
  exactly: `Booking Date`, `Value Date`, `Counterparty`, `Reference`, `Amount`, `Currency`,
  `Original Amount`, `Original Currency`, `Account Number`.
- **Derived id** — `content:{sha256-hex}:{occurrence_index}`, where `occurrence_index` is
  the zero-based count of byte-identical earlier rows within the same export.
- **Collision stance** — two byte-identical rows are two real transactions (e.g. two equal
  card payments booked the same day); the occurrence index keeps them distinct. The index
  follows file order, so replaying the same export derives the same ids. If the upstream
  ever reorders byte-identical rows between exports, ids can only swap among rows that are
  indistinguishable anyway, leaving the materialized canonical state unchanged. Rows that
  differ in any column can never collide: the hash covers every column, and control
  characters in field values are rejected, keeping the joined key material injective.

The derived value then feeds the standard `transaction_id` derivation, so ID-less sources
get the same replay-safety guarantees as every other bank.

### Left out of the canonical schema (deliberately)

- **Balances** — account balances are a separate concern with different freshness semantics;
  not needed for transaction aggregation.
- **FX conversion detail** (exchange rates, original amounts) — only the account-currency
  amount is canonical; each bank's original-currency detail (fjellvik `currencyExchange`,
  marlstone `originalCurrency`/`originalAmount`, taktwerk `Original Amount`/`Original
  Currency`) stays in the raw landing zone.
- **Merchant enrichment** (merchant IDs, MCC codes) — only one mock bank could supply
  anything like it; an enrichment layer can add it later without a schema break.
- **Intraday timestamps** — see `booking_date` above.
- **Consent/SCA and auth modeling** — out of scope per the brief; the regulatory shape is
  modeled, not implemented.
- **Running balances per transaction** — derivable downstream; storing them invites
  contradiction with the source.

## Alternatives considered

- **Single configurable generic mapper instead of adapter-per-bank** — rejected: field
  mappings alone cannot express structural divergence (nested vs flat, signed vs
  unsigned+indicator, CSV vs JSON); the config language would grow into a worse programming
  language.
- **Passthrough/union schema keeping all source fields** — rejected: pushes every bank's
  quirks onto every consumer, which is the exact problem a canonical layer exists to solve.
- **Random UUIDs or load-run IDs as transaction identity** — rejected: replays would create
  duplicates; idempotency requires identity to be a pure function of the source record.
- **Hashing transaction content as the idempotency key for every bank** — rejected for
  banks that provide stable IDs: the source ID survives upstream corrections to amount or
  description, a content hash does not. The condition "a source lacks stable transaction
  IDs" is not hypothetical — taktwerk has no transaction ID column today, so the
  content-derived key defined in the Decision section applies to it now, with the
  occurrence index resolving the identical-purchase collision that made content hashing
  unacceptable as the default.
- **`float` amounts** — rejected: binary floats cannot represent cents exactly; `Decimal`
  end to end.
- **Pydantic computed fields for the derived identifiers** — rejected after verification:
  breaks round-trip validation under `extra="forbid"` (details above).
- **Two mock banks instead of three** — rejected: two shapes can be reconciled with ad-hoc
  if/else; the third (CSV with localized formats) forces the adapter abstraction to be real.
- **Recorded live-API cassettes instead of hand-written fixtures** — rejected: requires real
  bank access to regenerate, leaks the credential dependency the brief forbids, and the
  fixtures must encode *designed* edge cases, not whatever a sandbox happened to return.

## Consequences

- Adding a bank = new fixture set + new adapter + its mapping tests; the canonical schema
  and all consumers stay untouched.
- Every load is replay-safe by construction: identity is deterministic before any storage
  layer exists, which Phase 2 (idempotent incremental loads) builds on directly.
- The canonical contract is strict (`extra="forbid"`, frozen, validated derivations), so
  schema drift surfaces as loud validation errors — the hook Phase 4's breaking-change
  detection attaches to.
- Hand-written fixtures must be maintained as the bank shapes evolve; fixture-integrity
  tests keep them honest but they remain curated artifacts.
- Excluding balances/FX detail/timestamps means revisiting this ADR if a consumer needs
  them; the raw landing zone preserves the data either way.
