"""Tests for the spend-mart CLI entry point (mart_cli.py).

Covers _format_rows formatting logic and main() round-trip so that mutations
in either function are caught.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from open_banking_pipeline.canonical import (
    CanonicalTransaction,
    SourceBank,
    TransactionCategory,
    TransactionStatus,
    derive_account_id,
    derive_transaction_id,
)
from open_banking_pipeline.ingestion.landing import LandingStore
from open_banking_pipeline.mart import SpendRow
from open_banking_pipeline.mart_cli import _format_rows, main


def _booked_tx(
    source_transaction_id: str,
    amount: float,
    booking_date: str,
    category: TransactionCategory,
) -> CanonicalTransaction:
    source_bank = SourceBank.MARLSTONE
    source_account_id = "MS-CLI-001"
    return CanonicalTransaction(
        transaction_id=derive_transaction_id(source_bank, source_account_id, source_transaction_id),
        account_id=derive_account_id(source_bank, source_account_id),
        source_bank=source_bank,
        source_account_id=source_account_id,
        source_transaction_id=source_transaction_id,
        status=TransactionStatus.BOOKED,
        booking_date=date.fromisoformat(booking_date),
        amount=Decimal(str(amount)),
        currency="EUR",
        category=category,
    )


class TestFormatRows:
    """Unit tests for _format_rows — independent of the store."""

    def test_empty_rows_returns_no_transactions_message(self) -> None:
        lines = _format_rows([])
        assert lines == ["No outflow transactions found."]

    def test_header_and_separator_are_present(self) -> None:
        row = SpendRow(
            year=2026,
            month=5,
            category=TransactionCategory.GROCERIES,
            total_spend=Decimal("64.67"),
            transaction_count=2,
        )
        lines = _format_rows([row])
        assert any("Month" in line and "Category" in line for line in lines)
        # At least two separator lines (one after header, one before total)
        separators = [line for line in lines if set(line.strip()) == {"-"}]
        assert len(separators) >= 2

    def test_month_name_lookup_uses_correct_index(self) -> None:
        row = SpendRow(
            year=2026,
            month=1,
            category=TransactionCategory.GROCERIES,
            total_spend=Decimal("10.00"),
            transaction_count=1,
        )
        lines = _format_rows([row])
        # "Jan" must appear in a data line (not header or separator)
        data_lines = [line for line in lines if "Jan" in line and "Category" not in line]
        assert data_lines, "expected 'Jan' in a data line"

    def test_total_line_is_correct_sum(self) -> None:
        rows = [
            SpendRow(
                year=2026,
                month=5,
                category=TransactionCategory.GROCERIES,
                total_spend=Decimal("64.67"),
                transaction_count=2,
            ),
            SpendRow(
                year=2026,
                month=5,
                category=TransactionCategory.DINING,
                total_spend=Decimal("48.75"),
                transaction_count=1,
            ),
        ]
        lines = _format_rows(rows)
        total_line = lines[-1]
        # 64.67 + 48.75 = 113.42
        assert "113.42" in total_line

    def test_total_line_is_zero_when_rows_empty_via_non_empty_path(self) -> None:
        # Confirm sum is correct for a single zero-ish value to prevent total=0 mutation.
        row = SpendRow(
            year=2026,
            month=3,
            category=TransactionCategory.TRANSPORT,
            total_spend=Decimal("12.34"),
            transaction_count=1,
        )
        lines = _format_rows([row])
        total_line = lines[-1]
        assert "12.34" in total_line


class TestMainRoundTrip:
    """Integration test: main() opens a real DuckDB store and prints output."""

    def test_main_returns_zero_and_produces_output(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        db_path = tmp_path / "cli_test.duckdb"
        with LandingStore.open(db_path) as store:
            store.insert_new_transactions(
                [
                    _booked_tx("c1", -23.40, "2026-05-01", TransactionCategory.GROCERIES),
                    _booked_tx("c2", -48.75, "2026-05-09", TransactionCategory.DINING),
                ]
            )

        exit_code = main(["--database", str(db_path)])

        assert exit_code == 0
        captured = capsys.readouterr()
        assert "groceries" in captured.out.lower() or "May" in captured.out
        assert "72.15" in captured.out  # 23.40 + 48.75 total

    def test_main_empty_store_prints_no_transactions_message(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        db_path = tmp_path / "empty.duckdb"
        with LandingStore.open(db_path):
            pass  # empty store, just initialize schema

        exit_code = main(["--database", str(db_path)])

        assert exit_code == 0
        assert "No outflow transactions found." in capsys.readouterr().out
