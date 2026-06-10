# ADR-0003: Mock-API interaction shapes and ingestion architecture

**Date:** 2026-06-10
**Status:** Accepted

## Context

ADR-0001 fixed the canonical schema and the fixture *data* shapes; Phase 1 has to fix how that
data is *served* and how it lands. Three things needed deciding: the interaction shape of each
mock bank API (pagination, error behavior — left open by ADR-0001), the retry/failure semantics
a connector must survive, and the landing store the idempotent runner writes to. A hard
constraint from the program brief: anything stochastic must be reproducible — a fixed seed must
produce byte-identical landed data.

## Decision

### Interaction shape per bank

Each bank is an in-process client serving the committed fixtures — no HTTP server, but the
*interaction contract* is the realistic part and differs deliberately per bank:

| Bank | Surface | Pagination | Error behavior |
| --- | --- | --- | --- |
| `fjellvik` | `request(path)` returning JSON text | Berlin-Group-style page links: each page embeds `transactions._links.next.href`; the connector follows links until absent (first account spans 3 pages of 4 entries) | `RateLimitError` modeling HTTP 429 + `Retry-After` (0.1 s), raised on planned request indexes |
| `marlstone` | `request(path)` returning JSON text | FDX-style cursor: each page carries an opaque `page.nextOffset`; the caller echoes it back as `?offset=` until it is `null` (first account spans 3 pages of 6) | none — its divergence budget is spent on cursor pagination and the unsigned-amount/`DEBIT`/`CREDIT` shape |
| `taktwerk` | `download_accounts_csv()` / `download_transactions_export()` returning whole-file text | none — whole-file download | **silent truncation**: a planned failure returns the file cut at 60% of its bytes without raising, like a dropped connection nobody checked Content-Length on |

Two error shapes on purpose: fjellvik fails *loudly at the transport* (an exception carrying the
server's retry hint), taktwerk fails *silently in the payload* (detection is the consumer's
job). The taktwerk adapter validates every download — a complete export ends with a newline and
every row has the full column count — and raises `TruncatedExportError` itself. A truncation cut
exactly at a row boundary would be undetectable without a row-count trailer or checksum; the mock
always cuts mid-row, and the limitation is accepted for Phase 1 rather than papered over.
Header drift is deliberately *not* retryable: it raises `ValueError` (schema change, a bug-class
event), never `TruncatedExportError` (an outage-class event).

### Deterministic fault injection

`PlannedFailures` holds a frozen set of failing request indexes per client; `from_seed` derives
the set from a seed via `random.Random(seed).sample`. No call to a mock ever consults an unseeded
RNG, so a fixed seed yields an identical request/failure/retry sequence — the reproducibility
test asserts the exported landing data is byte-identical across two seeded runs *and* identical
to a failure-free run.

### Retry policy

`fetch_with_retry` retries only the two operational errors: `RateLimitError` sleeps for the
server's `Retry-After` hint, `TruncatedExportError` backs off exponentially (0.5 s base,
doubling). Four attempts, then the error propagates. Anything else propagates immediately —
retrying a `ValueError` only hides an adapter bug.

### Landing store: DuckDB, first-write-wins, conflict-checked

Canonical accounts and transactions land in a single-file DuckDB database (`accounts`,
`transactions` tables, primary-keyed on the derived identifiers; amounts as `DECIMAL(18,4)`,
covering every ISO 4217 minor-unit scale). Insert semantics:

- **New identifier** → insert.
- **Known identifier, identical content** → no-op (this is what makes replays free).
- **Known identifier, different content** → `LandingConflictError`, batch rolled back —
  an upstream record changing under a stable ID must be a loud event, not a silent overwrite.
  Handling legitimate upstream corrections is explicitly deferred to a later phase.

Every batch runs inside a DuckDB transaction, so a conflicting batch lands nothing.

### Runner: synchronous, per-bank isolation

`run_ingestion` walks the banks sequentially. A bank whose operational errors survive every
retry is recorded as failed in the `IngestionReport` (string reason, surfaced by the CLI as a
non-zero exit) and the remaining banks still land; the next run completes the missing bank with
zero duplicates because identity is deterministic. Non-`BankApiError` exceptions propagate and
fail the whole run — bugs are not outages.

No queue, no scheduler, no concurrency in Phase 1: three banks and 46 transactions complete in
well under a second, and the report/exit-code contract is exactly what a future scheduler
(cron, Airflow, whatever) needs to trigger and monitor. The seam for that future is the
`Mapping[SourceBank, Extractor]` the runner consumes — a queue-based dispatcher would feed the
same contract.

## Alternatives considered

- **Real HTTP mocks (`responses`, `httpx.MockTransport`, or a local server) instead of
  in-process clients** — rejected: adds an HTTP client dependency and socket plumbing while the
  signal Phase 1 needs (divergent pagination, error shapes, body parsing) lives entirely in the
  interaction contract, which the in-process clients preserve at the body-text level. Revisit if
  a later phase wants to demonstrate transport-level concerns (TLS, timeouts, connection reuse).
- **Random failure injection without a seed plan** — rejected: irreproducible CI failures are
  worse than no failure testing; the brief makes seeded reproducibility a hard requirement.
- **Parquet files as the landing store** — rejected: append-only files make "insert if new,
  error if changed" a manual merge dance; DuckDB gives primary keys, transactions, and SQL for
  the Phase 3+ mart work in one zero-server file. Parquet remains a natural *export* format.
- **SQLite as the landing store** — rejected: works, but the analytics phases want columnar
  scans and DuckDB reads/writes Parquet natively; one engine end to end beats two.
- **`INSERT OR IGNORE` / `ON CONFLICT DO NOTHING` for idempotency** — rejected: it silently
  ignores the dangerous case (same ID, different content) along with the harmless one. The
  conflict check is the difference between idempotent and oblivious.
- **Retrying every exception** — rejected: a parsing bug that gets retried three times with
  backoff is still a parsing bug, just slower and harder to see.
- **Async/parallel extraction now** — rejected: three in-process banks have no latency to hide;
  concurrency would only complicate the failure-isolation story before contracts (Phase 4) pin
  the interfaces.

## Consequences

- Adding a bank = fixture set + mock client + adapter + wiring it into `build_extractors`; the
  runner, store, and report formats stay untouched.
- The retry layer is the single place outage semantics live; adapters stay pure mapping code
  plus loud validation.
- First-write-wins means upstream corrections (same source ID, amended amount) crash the load
  by design; a later phase must decide between versioning and reject-and-alert before that
  scenario becomes real.
- The landing store is canonical-shaped, not raw-shaped: the per-bank raw payloads are not
  persisted in Phase 1. If Phase 3's categorizer or an audit need raw replay beyond what the
  fixtures provide, a raw zone has to be added then.
- CI now executes the full pipeline twice per run (`make e2e`): once with seeded fault
  injection, once clean over the same store, proving retries and zero-duplicate replay on every
  push.
