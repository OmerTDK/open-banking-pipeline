"""Tests for the DuckDB landing store: idempotent, conflict-detecting inserts."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from open_banking_pipeline.canonical import (
    CanonicalAccount,
    CanonicalTransaction,
    SourceBank,
    TransactionStatus,
    derive_account_id,
    derive_transaction_id,
)
from open_banking_pipeline.ingestion.landing import (
    AmountScaleError,
    LandingConflictError,
    LandingStore,
)


def make_account(source_account_id: str = "FV-ACC-001") -> CanonicalAccount:
    return CanonicalAccount(
        account_id=derive_account_id(SourceBank.FJELLVIK, source_account_id),
        source_bank=SourceBank.FJELLVIK,
        source_account_id=source_account_id,
        display_name="Main Current Account",
        currency="EUR",
        iban="DE89370400440532013000",
    )


def make_transaction(
    source_transaction_id: str = "FV-TX-1001",
    amount: Decimal = Decimal("-23.40"),
) -> CanonicalTransaction:
    return CanonicalTransaction(
        transaction_id=derive_transaction_id(
            SourceBank.FJELLVIK, "FV-ACC-001", source_transaction_id
        ),
        account_id=derive_account_id(SourceBank.FJELLVIK, "FV-ACC-001"),
        source_bank=SourceBank.FJELLVIK,
        source_account_id="FV-ACC-001",
        source_transaction_id=source_transaction_id,
        status=TransactionStatus.BOOKED,
        booking_date=date(2026, 5, 2),
        value_date=date(2026, 5, 3),
        amount=amount,
        currency="EUR",
        counterparty_name="Greenfield Grocers",
        description="Card payment groceries",
        raw_category="PMNT-CCRD-POSD",
    )


@pytest.fixture
def store(tmp_path: Path) -> LandingStore:
    with LandingStore.open(tmp_path / "landing.duckdb") as landing_store:
        yield landing_store


class TestSchemaLifecycle:
    def test_open_creates_missing_parent_directories(self, tmp_path: Path) -> None:
        database_path = tmp_path / "data" / "local" / "landing.duckdb"

        with LandingStore.open(database_path) as landing_store:
            assert landing_store.count_transactions() == 0

        assert database_path.exists()

    def test_reopening_an_existing_store_preserves_rows(self, tmp_path: Path) -> None:
        database_path = tmp_path / "landing.duckdb"
        with LandingStore.open(database_path) as landing_store:
            landing_store.insert_new_transactions([make_transaction()])

        with LandingStore.open(database_path) as reopened_store:
            assert reopened_store.count_transactions() == 1


class TestIdempotentInserts:
    def test_new_accounts_are_inserted_and_counted(self, store: LandingStore) -> None:
        inserted = store.insert_new_accounts([make_account(), make_account("FV-ACC-002")])

        assert inserted == 2
        assert store.count_accounts() == 2

    def test_reinserting_identical_accounts_is_a_no_op(self, store: LandingStore) -> None:
        store.insert_new_accounts([make_account()])

        inserted_again = store.insert_new_accounts([make_account()])

        assert inserted_again == 0
        assert store.count_accounts() == 1

    def test_new_transactions_are_inserted_and_counted(self, store: LandingStore) -> None:
        inserted = store.insert_new_transactions(
            [make_transaction(), make_transaction("FV-TX-1002")]
        )

        assert inserted == 2
        assert store.count_transactions() == 2

    def test_reinserting_identical_transactions_is_a_no_op(self, store: LandingStore) -> None:
        store.insert_new_transactions([make_transaction()])

        inserted_again = store.insert_new_transactions([make_transaction()])

        assert inserted_again == 0
        assert store.count_transactions() == 1

    def test_stored_transaction_round_trips_to_an_equal_model(self, store: LandingStore) -> None:
        original = make_transaction()
        store.insert_new_transactions([original])

        restored = store.get_transaction(original.transaction_id)

        assert restored == original

    def test_get_transaction_returns_none_when_absent(self, store: LandingStore) -> None:
        assert store.get_transaction("0" * 64) is None


class TestConflictDetection:
    def test_same_id_with_different_content_is_rejected(self, store: LandingStore) -> None:
        store.insert_new_transactions([make_transaction()])
        conflicting = make_transaction(amount=Decimal("-99.99"))

        with pytest.raises(LandingConflictError, match="transaction"):
            store.insert_new_transactions([conflicting])

    def test_a_conflicting_batch_lands_nothing(self, store: LandingStore) -> None:
        store.insert_new_transactions([make_transaction()])
        batch = [make_transaction("FV-TX-2001"), make_transaction(amount=Decimal("-99.99"))]

        with pytest.raises(LandingConflictError):
            store.insert_new_transactions(batch)

        assert store.count_transactions() == 1
        assert store.get_transaction(batch[0].transaction_id) is None

    def test_conflicting_account_content_is_rejected(self, store: LandingStore) -> None:
        store.insert_new_accounts([make_account()])
        conflicting = make_account().model_copy(update={"display_name": "Renamed Account"})

        with pytest.raises(LandingConflictError, match="account"):
            store.insert_new_accounts([conflicting])


class TestAmountScaleGuard:
    def test_amount_with_more_than_four_decimal_places_is_rejected(
        self, store: LandingStore
    ) -> None:
        out_of_scale = make_transaction(amount=Decimal("1.23456"))

        with pytest.raises(AmountScaleError, match="decimal places"):
            store.insert_new_transactions([out_of_scale])

        assert store.count_transactions() == 0

    def test_amount_with_exactly_four_decimal_places_lands_unrounded(
        self, store: LandingStore
    ) -> None:
        boundary = make_transaction(amount=Decimal("-0.1234"))
        store.insert_new_transactions([boundary])

        restored = store.get_transaction(boundary.transaction_id)

        assert restored.amount == Decimal("-0.1234")


class TestDeterministicExport:
    def test_export_orders_by_transaction_id_and_parses_back(self, store: LandingStore) -> None:
        transactions = [make_transaction(f"FV-TX-{suffix}") for suffix in (3, 1, 2)]
        store.insert_new_transactions(transactions)

        exported = store.export_transactions_jsonl()

        lines = exported.decode("utf-8").splitlines()
        assert len(lines) == 3
        exported_ids = [json.loads(line)["transaction_id"] for line in lines]
        assert exported_ids == sorted(exported_ids)

    def test_export_is_byte_identical_across_equal_stores(self, tmp_path: Path) -> None:
        transactions = [make_transaction(f"FV-TX-{suffix}") for suffix in (1, 2)]
        exports = []
        for name in ("first.duckdb", "second.duckdb"):
            with LandingStore.open(tmp_path / name) as landing_store:
                landing_store.insert_new_transactions(transactions)
                exports.append(landing_store.export_transactions_jsonl())

        assert exports[0] == exports[1]

    def test_empty_store_exports_no_bytes(self, store: LandingStore) -> None:
        assert store.export_transactions_jsonl() == b""
