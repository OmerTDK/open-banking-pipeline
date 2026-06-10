"""Tests for the idempotent ingestion runner: zero duplicates, loud failures."""

from pathlib import Path

import pytest

from open_banking_pipeline.canonical import SourceBank
from open_banking_pipeline.ingestion.landing import LandingStore
from open_banking_pipeline.ingestion.retry import RetryPolicy
from open_banking_pipeline.ingestion.runner import (
    IngestionReport,
    build_extractors,
    run_ingestion,
)
from open_banking_pipeline.mock_banks.failures import PlannedFailures

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"

TOTAL_ACCOUNT_COUNT = 6
TOTAL_TRANSACTION_COUNT = 46
FJELLVIK_TRANSACTION_COUNT = 15
NON_FJELLVIK_TRANSACTION_COUNT = 31
REPRODUCIBILITY_SEED = 7


def sleep_immediately(_seconds: float) -> None:
    """No-op sleeper keeping retry-exercising tests instant."""


NO_SLEEP_POLICY = RetryPolicy(sleep=sleep_immediately)
ALWAYS_FAIL = PlannedFailures(failing_request_indexes=frozenset(range(1000)))


def ingest_fixtures(
    store: LandingStore,
    fjellvik_failures: PlannedFailures | None = None,
    taktwerk_failures: PlannedFailures | None = None,
) -> IngestionReport:
    extractors = build_extractors(
        FIXTURES_DIR,
        NO_SLEEP_POLICY,
        fjellvik_failures=fjellvik_failures,
        taktwerk_failures=taktwerk_failures,
    )
    return run_ingestion(extractors, store)


@pytest.fixture
def store(tmp_path: Path) -> LandingStore:
    with LandingStore.open(tmp_path / "landing.duckdb") as landing_store:
        yield landing_store


class TestFullIngestion:
    def test_all_three_banks_land_every_fixture_row(self, store: LandingStore) -> None:
        report = ingest_fixtures(store)

        assert report.is_success
        assert store.count_accounts() == TOTAL_ACCOUNT_COUNT
        assert store.count_transactions() == TOTAL_TRANSACTION_COUNT

    def test_report_carries_per_bank_loaded_counts(self, store: LandingStore) -> None:
        report = ingest_fixtures(store)

        loaded_by_bank = {
            result.source_bank: (result.accounts_loaded, result.transactions_loaded)
            for result in report.bank_results
        }
        assert loaded_by_bank == {
            SourceBank.FJELLVIK: (2, 15),
            SourceBank.MARLSTONE: (2, 16),
            SourceBank.TAKTWERK: (2, 15),
        }


class TestIdempotency:
    def test_running_twice_produces_zero_duplicates(self, store: LandingStore) -> None:
        ingest_fixtures(store)

        second_report = ingest_fixtures(store)

        assert second_report.is_success
        assert all(result.transactions_loaded == 0 for result in second_report.bank_results)
        assert all(result.accounts_loaded == 0 for result in second_report.bank_results)
        assert store.count_transactions() == TOTAL_TRANSACTION_COUNT

    def test_transient_failures_recover_within_one_run(self, store: LandingStore) -> None:
        report = ingest_fixtures(
            store,
            fjellvik_failures=PlannedFailures(failing_request_indexes=frozenset({1})),
            taktwerk_failures=PlannedFailures(failing_request_indexes=frozenset({0})),
        )

        assert report.is_success
        assert store.count_transactions() == TOTAL_TRANSACTION_COUNT


class TestPartialFailureRecovery:
    def test_one_bank_rate_limiting_out_does_not_block_the_others(
        self, store: LandingStore
    ) -> None:
        report = ingest_fixtures(store, fjellvik_failures=ALWAYS_FAIL)

        assert report.failed_banks == (SourceBank.FJELLVIK,)
        failed_result = report.bank_results[0]
        assert failed_result.source_bank is SourceBank.FJELLVIK
        assert "rate limited" in failed_result.failure_reason
        assert store.count_transactions() == NON_FJELLVIK_TRANSACTION_COUNT

    def test_retry_after_partial_failure_completes_without_duplicates(
        self, store: LandingStore
    ) -> None:
        ingest_fixtures(store, fjellvik_failures=ALWAYS_FAIL)

        recovery_report = ingest_fixtures(store)

        assert recovery_report.is_success
        loaded_by_bank = {
            result.source_bank: result.transactions_loaded
            for result in recovery_report.bank_results
        }
        assert loaded_by_bank == {
            SourceBank.FJELLVIK: FJELLVIK_TRANSACTION_COUNT,
            SourceBank.MARLSTONE: 0,
            SourceBank.TAKTWERK: 0,
        }
        assert store.count_transactions() == TOTAL_TRANSACTION_COUNT

    def test_persistent_truncation_fails_only_taktwerk(self, store: LandingStore) -> None:
        report = ingest_fixtures(store, taktwerk_failures=ALWAYS_FAIL)

        assert report.failed_banks == (SourceBank.TAKTWERK,)
        assert "truncated" in report.bank_results[-1].failure_reason

    def test_non_operational_errors_propagate_instead_of_being_reported(
        self, store: LandingStore
    ) -> None:
        def broken_extractor() -> None:
            raise ValueError("adapter bug")

        with pytest.raises(ValueError, match="adapter bug"):
            run_ingestion({SourceBank.FJELLVIK: broken_extractor}, store)


class TestReproducibility:
    def run_seeded_ingestion(self, database_path: Path, seed: int) -> bytes:
        extractors = build_extractors(
            FIXTURES_DIR,
            NO_SLEEP_POLICY,
            fjellvik_failures=PlannedFailures.from_seed(seed, request_count=6, failure_count=2),
            taktwerk_failures=PlannedFailures.from_seed(seed + 1, request_count=1, failure_count=1),
        )
        with LandingStore.open(database_path) as landing_store:
            report = run_ingestion(extractors, landing_store)
            assert report.is_success
            return landing_store.export_transactions_jsonl()

    def test_same_seed_produces_byte_identical_landing_data(self, tmp_path: Path) -> None:
        first_export = self.run_seeded_ingestion(tmp_path / "first.duckdb", REPRODUCIBILITY_SEED)
        second_export = self.run_seeded_ingestion(tmp_path / "second.duckdb", REPRODUCIBILITY_SEED)

        assert first_export == second_export

    def test_failure_schedule_does_not_change_landed_data(
        self, tmp_path: Path, store: LandingStore
    ) -> None:
        seeded_export = self.run_seeded_ingestion(tmp_path / "seeded.duckdb", REPRODUCIBILITY_SEED)

        ingest_fixtures(store)
        clean_export = store.export_transactions_jsonl()

        assert seeded_export == clean_export
