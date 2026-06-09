# Brief 05 — Open Banking / PSD2 Aggregation Pipeline

Working repo title: `open-banking-pipeline` (finalized in this project's own brainstorm).

## Mission

Build a multi-bank, PSD2-style account-aggregation pipeline for a fictional set of banks: ingest
transactions from several mock bank APIs into one canonical, categorized transaction schema, with
idempotent incremental loads and producer/consumer data contracts whose breaking changes are
detected in CI. Everything is fixture-driven — no real bank credentials, no live endpoints — so the
whole system is reproducible by anyone who clones the repo.

## Staff signal

**Axis F — quant / regulatory depth (EU-relevant integration).** Open Banking aggregation is the
canonical EU fintech integration problem: N upstream providers with deliberately divergent schemas,
pagination, and auth shapes, all forced into one governed canonical model. It clears the staff bar
because the hard parts are architectural, not mechanical:

- **Canonical schema design under divergence** — the mapping layer is a defensible-judgment
  artifact (ADR), not glue code.
- **Idempotency and replay-safety** — incremental loads that survive re-runs, restarts, and
  late-arriving data are a reliability-engineering signal, not a tutorial feature.
- **Data contracts with breaking-change detection in CI** — producer/consumer contracts make the
  pipeline's guarantees explicit and machine-enforced, which is exactly the "reliability
  engineering" evidence the portfolio thesis demands.

## Scope

**In:**

- Three or more mock bank APIs (fixture-driven), each with intentionally different schemas,
  pagination styles, and error behaviors.
- Per-bank extraction connectors with cursor-based, idempotent incremental loads (safe to re-run;
  replay produces identical state).
- One canonical transaction schema plus an explicit, tested mapping layer per bank.
- Deterministic transaction categorization (documented rules, testable, reproducible).
- Producer/consumer data contracts on the canonical schema, with breaking-change detection wired
  into CI (a demonstrated failing case: a bank changes a field, CI catches it before merge).
- Tests as first-class scope: contract tests, idempotency/replay tests, schema-drift tests.
- A small downstream analytics view (e.g. spend by category by month) proving the canonical layer
  is actually consumable.

**Out:**

- Real bank credentials, live PSD2 endpoints, consent/SCA flows, or any regulatory licensing
  concerns — the regulatory shape is modeled, not implemented.
- ML-based categorization (rules only; an ML upgrade is a documented future extension).
- Real-time/streaming ingestion — this is batch incremental by design.
- Full BI layer — one demo view is enough; dashboards belong to other projects.

## Architecture

1. **Mock bank APIs** — three fictional banks served from fixtures, each exposing a different
   transaction shape (field names, nesting, pagination, timestamp conventions) to force real
   canonicalization work.
2. **Extraction connectors** — one per bank; cursor-based incremental pulls, idempotent writes to a
   raw landing zone keyed so replays are no-ops.
3. **Landing (raw)** — per-bank raw storage, untouched, append-only.
4. **Canonical mapping layer** — transforms each bank's raw shape into the single canonical
   transaction schema; every mapping decision documented and tested.
5. **Categorization** — deterministic rule-based classification of canonical transactions into
   spend categories.
6. **Data contracts** — explicit producer/consumer contracts at the raw→canonical and
   canonical→consumer boundaries; CI diffs contract versions and fails on breaking changes.
7. **Consumption mart** — a small aggregated view demonstrating the canonical schema in use.
8. **CI/CD** — lint + full test suite + contract checks on every PR; an end-to-end pipeline run in
   CI is the proof of reproducibility.

## Build phases

- **Phase 0** — repo scaffold from the template, CI green, standards wired in; ADR for the
  canonical transaction schema.
- **Phase 1** — mock bank APIs + fixtures: three banks with divergent shapes.
- **Phase 2** — connectors with idempotent incremental loads into landing; replay tests.
- **Phase 3** — canonical mapping layer + categorization; schema-drift tests.
- **Phase 4** — data contracts + breaking-change detection in CI, including a demonstrated caught
  break.
- **Phase 5** — end-to-end run in CI, demo writeup, quantified results in the README.

Each phase ends with: an ADR, passing tests, and a README update.

## Stack

- **Language:** Python 3.12+ (connectors, mock APIs, contracts, categorization rules).
- **Tooling:** uv, ruff, pytest, pre-commit — per the standards pack.
- **Storage:** local-first (DuckDB or parquet landing zone) so the end-to-end run works in CI with
  zero cloud credentials; a cloud-warehouse target is an optional extension.
- **Contracts tooling and ingestion framework** (hand-rolled vs. an ingestion library, schema
  models vs. a contract spec format): decided in this project's brainstorm — the trade-off gets an
  ADR either way.

## Deployed means

Reproducible end-to-end run in CI plus a documented demo: every PR (and a scheduled run) executes
the full pipeline — mock APIs → connectors → canonical schema → categorization → mart — with
contract checks gating the merge. The README documents the demo with real numbers.

## Dependencies

None — fully standalone. It does not consume the flagship platform's generator or semantic layer,
which is deliberate: this project is schedulable last and is the designated drop if the program
kill-switch fires (quality over quantity — it gets dropped, not rushed).

## Definition of done

- [ ] README that tells the **system story**, with an architecture diagram.
- [ ] **ADRs** for each major design decision (the tradeoff, not just the choice).
- [ ] **Full CI green** — lint + tests on every PR.
- [ ] Meaningful **tests / data contracts** (not just `not_null`/`unique`).
- [ ] **Observability** where applicable (test results, freshness, anomalies).
- [ ] A **results section** with quantified outcomes (runtime, cost, test count, savings).
- [ ] **Generated docs** published.
- [ ] A short writeup of the **single hardest design decision**.
- [ ] Conforms to **Omer's coding standards** (§6).
- [ ] **Public** repo with a clean history once polished.
