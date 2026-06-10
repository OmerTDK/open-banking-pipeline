"""Validation tests for the canonical account and transaction models."""

import hashlib
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from open_banking_pipeline.canonical import (
    CanonicalAccount,
    CanonicalTransaction,
    SourceBank,
    TransactionCategory,
    TransactionStatus,
    derive_account_id,
    derive_content_source_transaction_id,
    derive_transaction_id,
)

OMIT = object()


def build_fields(defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = {**defaults, **overrides}
    return {name: value for name, value in merged.items() if value is not OMIT}


def make_account(**overrides: Any) -> CanonicalAccount:
    defaults: dict[str, Any] = {
        "source_bank": SourceBank.FJELLVIK,
        "source_account_id": "FV-ACC-001",
        "account_id": derive_account_id(SourceBank.FJELLVIK, "FV-ACC-001"),
        "display_name": "Main Current Account",
        "currency": "EUR",
        "iban": "DE89370400440532013000",
    }
    return CanonicalAccount(**build_fields(defaults, overrides))


def make_transaction(**overrides: Any) -> CanonicalTransaction:
    defaults: dict[str, Any] = {
        "source_bank": SourceBank.FJELLVIK,
        "source_account_id": "FV-ACC-001",
        "source_transaction_id": "FV-TX-1001",
        "account_id": derive_account_id(SourceBank.FJELLVIK, "FV-ACC-001"),
        "transaction_id": derive_transaction_id(SourceBank.FJELLVIK, "FV-ACC-001", "FV-TX-1001"),
        "status": TransactionStatus.BOOKED,
        "booking_date": date(2026, 5, 2),
        "value_date": date(2026, 5, 3),
        "amount": Decimal("-23.40"),
        "currency": "EUR",
        "counterparty_name": "Greenfield Grocers",
        "description": "Card payment groceries",
        "raw_category": "PMNT-CCRD-POSD",
        "category": TransactionCategory.GROCERIES,
    }
    return CanonicalTransaction(**build_fields(defaults, overrides))


class TestIdempotencyKeyDerivation:
    def test_derive_account_id_prefixes_source_bank(self) -> None:
        assert derive_account_id(SourceBank.FJELLVIK, "FV-ACC-001") == "fjellvik:FV-ACC-001"

    def test_derive_transaction_id_is_documented_sha256(self) -> None:
        expected = hashlib.sha256(b"fjellvik\x1fFV-ACC-001\x1fFV-TX-1001").hexdigest()

        derived = derive_transaction_id(SourceBank.FJELLVIK, "FV-ACC-001", "FV-TX-1001")

        assert derived == expected

    def test_derive_transaction_id_is_deterministic(self) -> None:
        first = derive_transaction_id(SourceBank.MARLSTONE, "MS-330011", "MS-TXN-88001")
        second = derive_transaction_id(SourceBank.MARLSTONE, "MS-330011", "MS-TXN-88001")

        assert first == second

    def test_derive_transaction_id_differs_across_banks(self) -> None:
        fjellvik_key = derive_transaction_id(SourceBank.FJELLVIK, "ACC-1", "TX-1")
        marlstone_key = derive_transaction_id(SourceBank.MARLSTONE, "ACC-1", "TX-1")

        assert fjellvik_key != marlstone_key

    def test_derive_transaction_id_differs_across_accounts(self) -> None:
        first_account = derive_transaction_id(SourceBank.TAKTWERK, "TW-7701", "TX-1")
        second_account = derive_transaction_id(SourceBank.TAKTWERK, "TW-7702", "TX-1")

        assert first_account != second_account


TAKTWERK_EXPORT_ROW = [
    "02.05.2026",
    "03.05.2026",
    "GREENFIELD GROCERS",
    "Kartenzahlung Lebensmittel",
    "-31,86",
    "EUR",
    "",
    "",
    "TW-7701",
]


class TestContentDerivedSourceTransactionId:
    def test_same_row_and_occurrence_derive_the_same_id(self) -> None:
        first = derive_content_source_transaction_id(TAKTWERK_EXPORT_ROW, 0)
        second = derive_content_source_transaction_id(TAKTWERK_EXPORT_ROW, 0)

        assert first == second

    def test_distinct_row_content_derives_distinct_ids(self) -> None:
        changed_amount_row = [*TAKTWERK_EXPORT_ROW[:4], "-31,87", *TAKTWERK_EXPORT_ROW[5:]]

        original = derive_content_source_transaction_id(TAKTWERK_EXPORT_ROW, 0)
        changed = derive_content_source_transaction_id(changed_amount_row, 0)

        assert original != changed

    def test_identical_rows_are_distinguished_by_occurrence_index(self) -> None:
        first_occurrence = derive_content_source_transaction_id(TAKTWERK_EXPORT_ROW, 0)
        second_occurrence = derive_content_source_transaction_id(TAKTWERK_EXPORT_ROW, 1)

        assert first_occurrence != second_occurrence

    def test_field_order_matters(self) -> None:
        swapped = derive_content_source_transaction_id(["a", "b"], 0)
        original = derive_content_source_transaction_id(["b", "a"], 0)

        assert swapped != original

    def test_negative_occurrence_index_rejected(self) -> None:
        with pytest.raises(ValueError, match="occurrence_index"):
            derive_content_source_transaction_id(TAKTWERK_EXPORT_ROW, -1)

    def test_empty_field_list_rejected(self) -> None:
        with pytest.raises(ValueError, match="field_values"):
            derive_content_source_transaction_id([], 0)

    def test_control_characters_in_field_values_rejected(self) -> None:
        with pytest.raises(ValueError, match="control character"):
            derive_content_source_transaction_id(["02.05.2026", "GROCERS\x1fEUR"], 0)

    def test_derived_id_builds_a_valid_canonical_transaction(self) -> None:
        source_transaction_id = derive_content_source_transaction_id(TAKTWERK_EXPORT_ROW, 0)

        transaction = make_transaction(
            source_bank=SourceBank.TAKTWERK,
            source_account_id="TW-7701",
            source_transaction_id=source_transaction_id,
            account_id=derive_account_id(SourceBank.TAKTWERK, "TW-7701"),
            transaction_id=derive_transaction_id(
                SourceBank.TAKTWERK, "TW-7701", source_transaction_id
            ),
        )

        assert transaction.source_transaction_id == source_transaction_id


class TestControlCharacterRejection:
    def test_derivation_rejects_separator_in_source_account_id(self) -> None:
        with pytest.raises(ValueError, match="control character"):
            derive_transaction_id(SourceBank.FJELLVIK, "FV-ACC\x1f001", "TX-1")

    def test_derivation_rejects_separator_in_source_transaction_id(self) -> None:
        with pytest.raises(ValueError, match="control character"):
            derive_transaction_id(SourceBank.FJELLVIK, "FV-ACC", "001\x1fTX-1")

    def test_account_derivation_rejects_separator(self) -> None:
        with pytest.raises(ValueError, match="control character"):
            derive_account_id(SourceBank.FJELLVIK, "FV-ACC\x1f001")

    def test_account_model_rejects_control_characters_in_source_account_id(self) -> None:
        with pytest.raises(ValidationError, match="control character"):
            make_account(source_account_id="FV-ACC\x1f001")

    def test_transaction_model_rejects_control_characters_in_source_account_id(self) -> None:
        with pytest.raises(ValidationError, match="control character"):
            make_transaction(source_account_id="FV-ACC\x1f001")

    def test_transaction_model_rejects_control_characters_in_source_transaction_id(self) -> None:
        with pytest.raises(ValidationError, match="control character"):
            make_transaction(source_transaction_id="FV-TX\n1001")

    def test_transaction_model_rejects_delete_character(self) -> None:
        with pytest.raises(ValidationError, match="control character"):
            make_transaction(source_transaction_id="FV-TX\x7f1001")


class TestCanonicalAccount:
    def test_valid_account_constructs(self) -> None:
        account = make_account()

        assert account.account_id == "fjellvik:FV-ACC-001"
        assert account.currency == "EUR"

    def test_account_id_must_match_derivation(self) -> None:
        with pytest.raises(ValidationError, match="account_id"):
            make_account(account_id="fjellvik:SOMETHING-ELSE")

    def test_empty_display_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_account(display_name="")

    def test_lowercase_currency_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_account(currency="eur")

    def test_account_is_immutable(self) -> None:
        account = make_account()

        with pytest.raises(ValidationError):
            account.display_name = "Renamed"

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_account(balance=Decimal("100.00"))

    def test_iban_is_optional(self) -> None:
        account = make_account(iban=None)

        assert account.iban is None


class TestCanonicalTransaction:
    def test_valid_booked_transaction_constructs(self) -> None:
        transaction = make_transaction()

        assert transaction.status is TransactionStatus.BOOKED
        assert transaction.amount == Decimal("-23.40")

    def test_transaction_id_must_match_derivation(self) -> None:
        with pytest.raises(ValidationError, match="transaction_id"):
            make_transaction(transaction_id="0" * 64)

    def test_account_id_must_match_derivation(self) -> None:
        with pytest.raises(ValidationError, match="account_id"):
            make_transaction(account_id="fjellvik:WRONG-ACCOUNT")

    def test_booked_transaction_requires_booking_date(self) -> None:
        with pytest.raises(ValidationError, match="booking_date"):
            make_transaction(booking_date=None)

    def test_pending_transaction_without_booking_date_is_valid(self) -> None:
        transaction = make_transaction(
            status=TransactionStatus.PENDING,
            booking_date=None,
            value_date=None,
        )

        assert transaction.status is TransactionStatus.PENDING
        assert transaction.booking_date is None

    def test_zero_amount_rejected(self) -> None:
        with pytest.raises(ValidationError, match="zero"):
            make_transaction(amount=Decimal("0.00"))

    def test_amount_preserves_decimal_precision(self) -> None:
        transaction = make_transaction(amount=Decimal("-1234.56"))

        assert transaction.amount == Decimal("-1234.56")

    def test_refund_amount_is_positive_inflow(self) -> None:
        refund = make_transaction(amount=Decimal("12.99"))

        assert refund.amount > 0

    def test_lowercase_currency_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_transaction(currency="usd")

    def test_wrong_length_currency_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_transaction(currency="EURO")

    def test_empty_source_transaction_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_transaction(
                source_transaction_id="",
                transaction_id=derive_transaction_id(SourceBank.FJELLVIK, "FV-ACC-001", ""),
            )

    def test_category_defaults_to_uncategorized(self) -> None:
        transaction = make_transaction(category=OMIT)

        assert transaction.category is TransactionCategory.UNCATEGORIZED

    def test_unknown_category_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_transaction(category="crypto")

    def test_counterparty_is_optional(self) -> None:
        transaction = make_transaction(counterparty_name=None, counterparty_account=None)

        assert transaction.counterparty_name is None

    def test_transaction_is_immutable(self) -> None:
        transaction = make_transaction()

        with pytest.raises(ValidationError):
            transaction.amount = Decimal("1.00")

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_transaction(merchant_code="5411")

    def test_dict_round_trip_preserves_equality(self) -> None:
        transaction = make_transaction()

        restored = CanonicalTransaction.model_validate(transaction.model_dump())

        assert restored == transaction

    def test_json_round_trip_preserves_equality(self) -> None:
        transaction = make_transaction()

        restored = CanonicalTransaction.model_validate_json(transaction.model_dump_json())

        assert restored == transaction
