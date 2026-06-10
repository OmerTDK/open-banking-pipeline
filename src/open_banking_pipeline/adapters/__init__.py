"""Per-bank adapters mapping mock client output to the canonical models.

Divergence lives here: each adapter owns its bank's pagination, parsing,
sign convention, and identifier derivation. The canonical layer knows
nothing about any bank's quirks (ADR-0001).
"""

from dataclasses import dataclass

from open_banking_pipeline.canonical import CanonicalAccount, CanonicalTransaction


@dataclass(frozen=True)
class BankExtract:
    """Everything one bank yielded in a single extraction run."""

    accounts: tuple[CanonicalAccount, ...]
    transactions: tuple[CanonicalTransaction, ...]
