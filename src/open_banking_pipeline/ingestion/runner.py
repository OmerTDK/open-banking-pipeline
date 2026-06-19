"""Synchronous ingestion runner: all banks into one landing store, idempotently.

Failure isolation (ADR-0003): operational bank failures (rate limits or
truncated downloads that survive every retry) mark that bank failed in the
report and never block the other banks; a rerun completes the missing bank
with zero duplicates. Anything that is not a ``BankApiError`` is a bug, not
an outage, and propagates immediately.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from open_banking_pipeline.adapters import BankExtract
from open_banking_pipeline.adapters import fjellvik as fjellvik_adapter
from open_banking_pipeline.adapters import marlstone as marlstone_adapter
from open_banking_pipeline.adapters import taktwerk as taktwerk_adapter
from open_banking_pipeline.canonical import SourceBank
from open_banking_pipeline.categorization import apply_category
from open_banking_pipeline.errors import BankApiError
from open_banking_pipeline.ingestion.landing import LandingStore
from open_banking_pipeline.ingestion.retry import RetryPolicy
from open_banking_pipeline.mock_banks.failures import PlannedFailures
from open_banking_pipeline.mock_banks.fjellvik import FjellvikMockBank
from open_banking_pipeline.mock_banks.marlstone import MarlstoneMockBank
from open_banking_pipeline.mock_banks.taktwerk import TaktwerkMockBank

Extractor = Callable[[], BankExtract]


@dataclass(frozen=True)
class BankIngestionResult:
    """Outcome of ingesting one bank: loaded counts or a failure reason."""

    source_bank: SourceBank
    accounts_loaded: int
    transactions_loaded: int
    failure_reason: str | None = None

    @property
    def is_success(self) -> bool:
        return self.failure_reason is None


@dataclass(frozen=True)
class IngestionReport:
    """Per-bank outcomes of one ingestion run."""

    bank_results: tuple[BankIngestionResult, ...]

    @property
    def failed_banks(self) -> tuple[SourceBank, ...]:
        return tuple(result.source_bank for result in self.bank_results if not result.is_success)

    @property
    def is_success(self) -> bool:
        return not self.failed_banks


def run_ingestion(
    extractors: Mapping[SourceBank, Extractor],
    store: LandingStore,
) -> IngestionReport:
    """Extract every bank and land the canonical records idempotently."""
    bank_results = []
    for source_bank, extract_bank in extractors.items():
        try:
            extract = extract_bank()
        except BankApiError as error:
            bank_results.append(
                BankIngestionResult(
                    source_bank=source_bank,
                    accounts_loaded=0,
                    transactions_loaded=0,
                    failure_reason=str(error),
                )
            )
            continue
        categorized_transactions = tuple(apply_category(tx) for tx in extract.transactions)
        bank_results.append(
            BankIngestionResult(
                source_bank=source_bank,
                accounts_loaded=store.insert_new_accounts(extract.accounts),
                transactions_loaded=store.insert_new_transactions(categorized_transactions),
            )
        )
    return IngestionReport(bank_results=tuple(bank_results))


def build_extractors(
    fixtures_dir: Path,
    retry_policy: RetryPolicy,
    fjellvik_failures: PlannedFailures | None = None,
    taktwerk_failures: PlannedFailures | None = None,
) -> dict[SourceBank, Extractor]:
    """Wire the three mock banks to their adapters in deterministic run order."""
    fjellvik_bank = FjellvikMockBank(fixtures_dir, planned_failures=fjellvik_failures)
    marlstone_bank = MarlstoneMockBank(fixtures_dir)
    taktwerk_bank = TaktwerkMockBank(fixtures_dir, planned_failures=taktwerk_failures)
    return {
        SourceBank.FJELLVIK: partial(fjellvik_adapter.extract, fjellvik_bank, retry_policy),
        SourceBank.MARLSTONE: partial(marlstone_adapter.extract, marlstone_bank, retry_policy),
        SourceBank.TAKTWERK: partial(taktwerk_adapter.extract, taktwerk_bank, retry_policy),
    }
