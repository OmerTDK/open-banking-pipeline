# ADR-0004: Data-contract format and breaking-change detection

**Date:** 2026-06-10
**Status:** Accepted

## Context

ADR-0001 made the canonical schema strict so that drift surfaces as loud validation errors;
this phase makes the guarantee explicit and machine-enforced. The pipeline now has two
producer boundaries worth contracting — the canonical pydantic models every adapter emits,
and the landing tables every downstream reader queries — and a first declared consumer (the
Phase 3 categorization engine). Three things needed deciding: the contract artifact format,
the compatibility rules a CI check enforces, and how consumers get a say when a producer
breaks a field they depend on.

## Decision

### Contracts are generated from code, never hand-written

Four subjects, one JSON artifact each under `contracts/`:

| Subject | Derived from |
| --- | --- |
| `canonical_account` | `CanonicalAccount` (pydantic introspection) |
| `canonical_transaction` | `CanonicalTransaction` (pydantic introspection) |
| `landing_accounts` | the `LandingColumn` specs that also build the DuckDB DDL |
| `landing_transactions` | the `LandingColumn` specs that also build the DuckDB DDL |

Each artifact records, per field: `name`, `type` (normalized scalar or lowercased SQL
type), `nullable`, `required` (must be provided by the producer), `primary_key` (the
uniqueness guarantee the landing DDL declares; always `false` for pydantic-derived
contracts, which have no key concept), `enum_values` (the full value set for enum-typed
fields), and `doc` (the semantic note, from the pydantic field description). Field
attribute names mirror standard contract vocabulary (JSON Schema, data-contract specs)
rather than the codebase's `is_*` boolean style, so the artifact reads natively to
external tooling. Artifacts are canonical JSON — sorted keys, fixed indent, trailing newline —
so regeneration of an unchanged contract is byte-identical and any diff is a real change.
A `contract_format` integer versions the artifact format itself, separately from the
schema versions it carries.

Code-derived beats hand-written because a hand-written contract is a second source of
truth that drifts silently: nothing forces it to change when the model changes, so it
documents the schema the team *remembers*, not the one that ships. Generating from the
pydantic models and the DDL specs means the contract cannot disagree with the code — the
only way to change the published contract is to change the actual schema, and the only way
to change the schema without updating the contract is to fail CI.

### Compatibility rules: classified from the union of both perspectives

A change is breaking if it breaks *either* writers (adapters constructing records) or
readers (consumers of landed data). That union stance resolves the direction-dependent
cases conservatively:

| Change | Classification |
| --- | --- |
| Field removed | breaking |
| Field type changed (any direction) | breaking |
| Nullability changed (either direction) | breaking — widening breaks readers, narrowing breaks writers |
| Optional field became required | breaking |
| Primary key added or removed | breaking — adding breaks writers (uniqueness now enforced), removing breaks readers (uniqueness guarantee gone) |
| Enum value removed | breaking |
| Enum constraint added or removed entirely | breaking |
| New required field | breaking |
| New optional field (nullable or defaulted) | non-breaking |
| Enum value added | non-breaking — consumers are obliged to tolerate unknown values |
| Required field became optional | non-breaking |
| Fields reordered (same field set) | non-breaking — named access is unaffected, but the physical column order of the deployed shape changes, so it must ship under a bump |
| Semantic-note (doc) change | documentation |

Version rules are semver-ish, enforced per subject against `versions.py` (the declared
version is code too): breaking requires a **major** bump, non-breaking schema changes at
least a **minor** bump, documentation changes at least a **patch** bump, and a version
must never change without a corresponding change (or go backwards).

### The detector and its two enforcement levels

`open-banking-contracts check` regenerates the contracts from code, diffs them against the
committed artifacts, prints every classified change, and exits non-zero when a change lacks
the bump its classification demands — a breaking change passes only with a major bump.
`--require-fresh` (what `make contracts-check` and CI run) additionally fails while the
committed artifacts differ from code at all, so a PR can only merge with regenerated
artifacts. `open-banking-contracts generate` runs the same assessment first and refuses to
overwrite artifacts over an unbumped change — the version bump in `versions.py` has to come
before the artifact refresh, which keeps the semver discipline on the only sanctioned path
for changing `contracts/`.

### Consumer manifests veto breaking changes to pinned fields

Each consumer commits a manifest under `contracts/consumers/` pinning the exact fields it
reads per subject, plus the contract version it has acknowledged. A breaking change to a
pinned field is a hard failure *even with a major bump* unless the consumer's
`acknowledged_version` covers the new version — forcing the ack (or the un-pinning) to land
in the same change set as the break, which is the coordination data contracts exist to
prove. Breaking changes to unpinned fields need only the major bump. Manifests are
validated on every check: pinning a nonexistent field or acknowledging a version ahead of
the contract fails immediately. First manifest: the categorization engine, pinning the
seven `canonical_transaction` fields it will read.

## Alternatives considered

- **Hand-written contract files (YAML/markdown)** — rejected: second source of truth;
  drifts the moment someone edits the model without remembering the contract. The whole
  point is that forgetting must be impossible.
- **An off-the-shelf contract spec (datacontract spec, ODCS)** — rejected for now: both
  are serialization formats, not enforcement — the diff/classification/CI logic would still
  be ours, and the specs drag in warehouse/server concepts (SLAs, endpoints) this repo
  doesn't have. The artifact is one `json.dumps` away from any such format if a tool ever
  wants it.
- **pydantic's `model_json_schema()` as the artifact** — rejected: its output mixes the
  compatibility surface with validation noise (titles, `$defs`, constraint encodings), so
  diffs would conflate cosmetic schema-emission changes with real breaks; it also covers
  only the pydantic side, not the landing DDL.
- **Avro/protobuf + a schema registry (buf-style)** — rejected: real compatibility
  tooling, but it would make the wire format the contract. This pipeline's boundaries are
  pydantic models and DuckDB tables; introducing an IDL just to diff it is indirection.
- **Diffing against the git base branch instead of committed artifacts** — deferred: it
  would close the residual loophole below without trusting the working tree, but it makes
  the detector depend on git state (fetch depth, merge-base resolution) and tests need repo
  fixtures. The committed-artifact baseline plus generate-refusal covers every honest
  workflow; the git baseline is the natural extension if that trust assumption ever fails.
- **Treating enum-value additions as breaking** — rejected: it would force a major bump
  for every new bank or spend category. The cost is a documented consumer obligation:
  readers must tolerate unknown enum values.

## Consequences

- Schema changes are now two-step by construction: bump `versions.py`, run
  `make contracts-generate`, commit both — CI fails any other path, including the
  forgot-to-regenerate and the bumped-but-stale states.
- A breaking change to a consumed field additionally requires touching the consumer's
  manifest, so producer and consumer move in one reviewed change set.
- Known limitation: the detector trusts the committed artifacts as the baseline. Someone
  who hand-edits an artifact *and* the matching version in code can fake continuity —
  visible in review, invisible to the tool. The git-baseline extension above closes this
  if it ever matters.
- Constraint details below the type level (regex patterns, `min_length`) are not part of
  the contract surface yet; narrowing the currency pattern would not be flagged. Adding
  constraint capture is a format-version bump (`contract_format: 2`).
- Landing-table fields carry no semantic notes (`doc: null`) — the DDL has nowhere to
  declare them; the canonical-model contracts hold the semantics.
