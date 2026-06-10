# open-banking-pipeline

Multi-bank PSD2-style ingestion into a canonical, categorized transaction schema with data contracts

> Status: 🚧 under construction — not yet at definition-of-done.

Phase 0 done: canonical pydantic v2 schema with deterministic idempotency keys, fixture sets for three divergent mock banks (PSD2-style JSON, FDX-style JSON, legacy CSV), 71 passing tests, ADR-0001.

Phase 1 done: three in-process mock bank APIs (page-link, cursor, and whole-file-download shapes) with seeded 429/truncation fault injection, per-bank adapters into the canonical schema, retry with backoff, and an idempotent DuckDB landing store — `make ingest` lands all 46 fixture transactions, a second run lands zero duplicates; 166 passing tests, ADR-0003.

## Why this exists

<!-- System narrative: the problem, why it is interesting, what it demonstrates. -->

## Architecture

<!-- Diagram + one paragraph per component. -->

## Results

<!-- Quantified outcomes: runtime, cost, test count, data volumes. Real numbers only. -->

## Design decisions

See [docs/adr/](docs/adr/) — each major decision documented with its trade-offs.

## Quickstart

<!-- Reproducible setup: clone → install → run end-to-end. -->

## Standards

Engineering conventions in [standards/](standards/) govern all code in this repo.
