"""Tests for the ingestion command-line interface."""

from pathlib import Path

import pytest

from open_banking_pipeline.canonical import SourceBank
from open_banking_pipeline.cli import exit_code_for, main
from open_banking_pipeline.ingestion.landing import LandingStore
from open_banking_pipeline.ingestion.runner import BankIngestionResult, IngestionReport

TOTAL_TRANSACTION_COUNT = 46


def sleep_immediately(_seconds: float) -> None:
    """No-op sleeper keeping retry-exercising CLI tests instant."""


def run_cli(database_path: Path, *extra_arguments: str) -> int:
    return main(["--database", str(database_path), *extra_arguments], sleep=sleep_immediately)


class TestCliIngestion:
    def test_ingests_all_banks_and_exits_zero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        database_path = tmp_path / "landing.duckdb"

        exit_code = run_cli(database_path)

        assert exit_code == 0
        captured_output = capsys.readouterr().out
        assert "fjellvik" in captured_output
        assert "transactions +15" in captured_output
        assert f"{TOTAL_TRANSACTION_COUNT} transactions" in captured_output
        with LandingStore.open(database_path) as store:
            assert store.count_transactions() == TOTAL_TRANSACTION_COUNT

    def test_second_invocation_is_idempotent(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        database_path = tmp_path / "landing.duckdb"
        run_cli(database_path)
        capsys.readouterr()

        exit_code = run_cli(database_path)

        assert exit_code == 0
        captured_output = capsys.readouterr().out
        assert "transactions +0" in captured_output
        assert f"{TOTAL_TRANSACTION_COUNT} transactions" in captured_output

    def test_seeded_failure_injection_still_lands_everything(self, tmp_path: Path) -> None:
        database_path = tmp_path / "landing.duckdb"

        exit_code = run_cli(database_path, "--failure-seed", "7")

        assert exit_code == 0
        with LandingStore.open(database_path) as store:
            assert store.count_transactions() == TOTAL_TRANSACTION_COUNT

    def test_retry_waits_use_the_injected_sleep(self, tmp_path: Path) -> None:
        sleep_calls: list[float] = []

        exit_code = main(
            ["--database", str(tmp_path / "landing.duckdb"), "--failure-seed", "7"],
            sleep=sleep_calls.append,
        )

        assert exit_code == 0
        assert sleep_calls


class TestExitCodes:
    def test_success_report_maps_to_zero(self) -> None:
        report = IngestionReport(
            bank_results=(
                BankIngestionResult(
                    source_bank=SourceBank.FJELLVIK,
                    accounts_loaded=2,
                    transactions_loaded=15,
                ),
            )
        )

        assert exit_code_for(report) == 0

    def test_any_failed_bank_maps_to_one(self) -> None:
        report = IngestionReport(
            bank_results=(
                BankIngestionResult(
                    source_bank=SourceBank.FJELLVIK,
                    accounts_loaded=0,
                    transactions_loaded=0,
                    failure_reason="rate limited; retry after 0.1s",
                ),
            )
        )

        assert exit_code_for(report) == 1
