"""Tests for the spend-by-category-by-month consumption mart.

The mart queries the DuckDB landing store and returns an ordered, aggregated
view of outflow spend per category per month.  Tests use an in-memory store
seeded with purpose-built transactions so the expected aggregates are exact.
"""

from datetime import date
from decimal import Decimal

import duckdb
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
from open_banking_pipeline.mart import SpendRow, build_spend_mart


def _tx(
    source_transaction_id: str,
    amount: float,
    booking_date: str,
    category: TransactionCategory,
    description: str | None = None,
) -> CanonicalTransaction:
    source_bank = SourceBank.MARLSTONE
    source_account_id = "MS-550033"
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
        description=description,
        category=category,
    )


@pytest.fixture
def store_with_spend_data() -> LandingStore:
    """In-memory store seeded with known outflows, one inflow, and one null date."""
    conn = duckdb.connect(":memory:")
    store = LandingStore(conn)
    store.initialize_schema()
    transactions = [
        # May groceries: two rows -> 23.40 + 41.27 = 64.67
        _tx("t1", -23.40, "2026-05-01", TransactionCategory.GROCERIES, "Card payment groceries"),
        _tx("t2", -41.27, "2026-05-04", TransactionCategory.GROCERIES, "POS GREENFIELD GROCERS"),
        # May dining
        _tx("t3", -48.75, "2026-05-09", TransactionCategory.DINING, "Card payment restaurant"),
        # June groceries
        _tx("t4", -31.50, "2026-06-02", TransactionCategory.GROCERIES, "Supermarket run"),
        # Salary inflow -- must be excluded from outflow mart
        _tx("t5", 2450.00, "2026-05-05", TransactionCategory.SALARY, "Salary May 2026"),
        # Transfer outflow
        _tx("t6", -500.00, "2026-05-03", TransactionCategory.TRANSFER, "Eigenuebertrag"),
    ]
    store.insert_new_transactions(transactions)
    return store


class TestBuildSpendMart:
    def test_returns_list_of_spend_rows(self, store_with_spend_data: LandingStore) -> None:
        rows = build_spend_mart(store_with_spend_data)
        assert isinstance(rows, list)
        assert all(isinstance(row, SpendRow) for row in rows)

    def test_excludes_inflows(self, store_with_spend_data: LandingStore) -> None:
        rows = build_spend_mart(store_with_spend_data)
        categories = {row.category for row in rows}
        assert TransactionCategory.SALARY not in categories

    def test_aggregates_same_category_same_month(self, store_with_spend_data: LandingStore) -> None:
        rows = build_spend_mart(store_with_spend_data)
        may_groceries = next(
            (
                r
                for r in rows
                if r.year == 2026 and r.month == 5 and r.category == TransactionCategory.GROCERIES
            ),
            None,
        )
        assert may_groceries is not None
        # 23.40 + 41.27 = 64.67, stored as positive spend
        assert may_groceries.total_spend == Decimal("64.67")
        assert may_groceries.transaction_count == 2

    def test_separates_different_months(self, store_with_spend_data: LandingStore) -> None:
        rows = build_spend_mart(store_with_spend_data)
        grocery_rows = [r for r in rows if r.category == TransactionCategory.GROCERIES]
        months = {r.month for r in grocery_rows}
        assert months == {5, 6}

    def test_total_spend_is_positive(self, store_with_spend_data: LandingStore) -> None:
        """Spend amounts must be positive (abs value of outflows)."""
        rows = build_spend_mart(store_with_spend_data)
        for row in rows:
            assert row.total_spend > 0

    def test_ordered_by_year_month_spend_desc(self, store_with_spend_data: LandingStore) -> None:
        rows = build_spend_mart(store_with_spend_data)
        for i in range(len(rows) - 1):
            current = (rows[i].year, rows[i].month, -rows[i].total_spend)
            following = (rows[i + 1].year, rows[i + 1].month, -rows[i + 1].total_spend)
            assert current <= following

    def test_empty_store_returns_empty_list(self) -> None:
        conn = duckdb.connect(":memory:")
        empty_store = LandingStore(conn)
        empty_store.initialize_schema()
        assert build_spend_mart(empty_store) == []

    def test_spend_row_fields(self, store_with_spend_data: LandingStore) -> None:
        rows = build_spend_mart(store_with_spend_data)
        row = rows[0]
        assert isinstance(row.year, int)
        assert isinstance(row.month, int)
        assert isinstance(row.category, TransactionCategory)
        assert isinstance(row.total_spend, Decimal)
        assert isinstance(row.transaction_count, int)

    def test_transfer_outflow_appears_in_mart(self, store_with_spend_data: LandingStore) -> None:
        rows = build_spend_mart(store_with_spend_data)
        transfer_row = next(
            (r for r in rows if r.category == TransactionCategory.TRANSFER),
            None,
        )
        assert transfer_row is not None
        assert transfer_row.total_spend == Decimal("500.00")


class TestSpendMartWithRealFixtures:
    """Smoke test: mart builds without error over the real landed fixture data."""

    def test_mart_builds_from_real_landing_store(self, tmp_path: pytest.TempPathFactory) -> None:
        """Full ingest then mart -- exercises the end-to-end categorization path."""
        import subprocess
        from pathlib import Path

        db_path = tmp_path / "mart_test.duckdb"
        repo_root = Path(__file__).parent.parent
        result = subprocess.run(
            [
                "uv",
                "run",
                "open-banking-ingest",
                "--database",
                str(db_path),
            ],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
        )
        assert result.returncode == 0, result.stderr

        store = LandingStore.open(db_path)
        rows = build_spend_mart(store)
        store.close()

        # 46 fixture transactions; only outflows appear in the mart
        assert len(rows) > 0
        # All rows have positive spend
        assert all(row.total_spend > 0 for row in rows)
