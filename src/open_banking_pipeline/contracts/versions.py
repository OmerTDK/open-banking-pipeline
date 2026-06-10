"""Declared contract versions — the single bump point for schema changes.

Bump rules (ADR-0004): a breaking change requires a major bump, a non-breaking
schema change requires at least a minor bump, a semantic-note-only change
requires at least a patch bump. The contract checker refuses unbumped changes
and refuses bumps without changes.
"""

CONTRACT_VERSIONS = {
    "canonical_account": "1.0.0",
    "canonical_transaction": "1.0.0",
    "landing_accounts": "1.0.0",
    "landing_transactions": "1.0.0",
}
