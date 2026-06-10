"""Integrity tests for the three mock-bank fixture sets.

Each bank deliberately exposes a different shape:

- fjellvik: Berlin-Group/PSD2-style JSON (nested booked/pending arrays,
  string amounts inside a transactionAmount object, ISO dates).
- marlstone: FDX-style JSON (flat camelCase entries, numeric amounts with a
  DEBIT/CREDIT indicator, ISO-8601 UTC timestamps, POSTED/PENDING status).
- taktwerk: legacy CSV export (semicolon-delimited, dd.mm.yyyy dates,
  decimal-comma amounts, booked transactions only).
"""

import csv
import json
import re
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"

MINIMUM_ACCOUNTS_PER_BANK = 2
MINIMUM_TRANSACTIONS_PER_BANK = 10
MAXIMUM_TRANSACTIONS_PER_BANK = 20

TAKTWERK_EXPECTED_HEADER = [
    "Booking Date",
    "Value Date",
    "Counterparty",
    "Reference",
    "Amount",
    "Currency",
    "Account Number",
]
LEGACY_DATE_PATTERN = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")
DECIMAL_COMMA_AMOUNT_PATTERN = re.compile(r"^-?\d{1,3}(\.\d{3})*,\d{2}$")


def load_json(relative_path: str) -> Any:
    return json.loads((FIXTURES_DIR / relative_path).read_text(encoding="utf-8"))


def load_fjellvik_transactions() -> tuple[list[dict], list[dict]]:
    booked: list[dict] = []
    pending: list[dict] = []
    for path in sorted((FIXTURES_DIR / "fjellvik").glob("transactions_*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        booked.extend(payload["transactions"]["booked"])
        pending.extend(payload["transactions"]["pending"])
    return booked, pending


def load_marlstone_transactions() -> list[dict]:
    payload = load_json("marlstone/transactions.json")
    return [entry["depositTransaction"] for entry in payload["transactions"]]


def load_taktwerk_rows(file_name: str) -> tuple[list[str], list[dict[str, str]]]:
    path = FIXTURES_DIR / "taktwerk" / file_name
    with path.open(encoding="utf-8", newline="") as export_file:
        reader = csv.DictReader(export_file, delimiter=";")
        header = list(reader.fieldnames or [])
        rows = list(reader)
    return header, rows


class TestFjellvikFixtures:
    def test_accounts_parse_with_expected_psd2_fields(self) -> None:
        accounts = load_json("fjellvik/accounts.json")["accounts"]

        assert len(accounts) >= MINIMUM_ACCOUNTS_PER_BANK
        for account in accounts:
            assert account["resourceId"]
            assert account["iban"]
            assert account["currency"]

    def test_transaction_count_within_documented_range(self) -> None:
        booked, pending = load_fjellvik_transactions()

        total = len(booked) + len(pending)
        assert MINIMUM_TRANSACTIONS_PER_BANK <= total <= MAXIMUM_TRANSACTIONS_PER_BANK

    def test_contains_booked_and_pending_transactions(self) -> None:
        booked, pending = load_fjellvik_transactions()

        assert booked
        assert pending

    def test_amounts_are_strings_inside_transaction_amount_object(self) -> None:
        booked, pending = load_fjellvik_transactions()

        for transaction in booked + pending:
            amount_object = transaction["transactionAmount"]
            assert isinstance(amount_object["amount"], str)
            assert amount_object["currency"]

    def test_contains_refund_edge_case(self) -> None:
        booked, _ = load_fjellvik_transactions()

        refunds = [
            transaction
            for transaction in booked
            if "refund" in transaction["remittanceInformationUnstructured"].lower()
            and not transaction["transactionAmount"]["amount"].startswith("-")
        ]
        assert refunds

    def test_contains_foreign_currency_edge_case(self) -> None:
        booked, _ = load_fjellvik_transactions()

        foreign = [
            transaction
            for transaction in booked
            if transaction["transactionAmount"]["currency"] != "EUR"
        ]
        assert foreign

    def test_booked_transactions_have_iso_booking_dates(self) -> None:
        booked, _ = load_fjellvik_transactions()

        for transaction in booked:
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", transaction["bookingDate"])


class TestMarlstoneFixtures:
    def test_accounts_parse_with_expected_fdx_fields(self) -> None:
        accounts = load_json("marlstone/accounts.json")["accounts"]

        assert len(accounts) >= MINIMUM_ACCOUNTS_PER_BANK
        for entry in accounts:
            deposit_account = entry["depositAccount"]
            assert deposit_account["accountId"]
            assert deposit_account["currency"]["currencyCode"]

    def test_transaction_count_within_documented_range(self) -> None:
        transactions = load_marlstone_transactions()

        count = len(transactions)
        assert MINIMUM_TRANSACTIONS_PER_BANK <= count <= MAXIMUM_TRANSACTIONS_PER_BANK

    def test_contains_posted_and_pending_statuses(self) -> None:
        statuses = {transaction["status"] for transaction in load_marlstone_transactions()}

        assert "POSTED" in statuses
        assert "PENDING" in statuses

    def test_amounts_are_unsigned_numbers_with_debit_credit_indicator(self) -> None:
        for transaction in load_marlstone_transactions():
            assert isinstance(transaction["amount"], int | float)
            assert transaction["amount"] > 0
            assert transaction["debitCreditMemo"] in {"DEBIT", "CREDIT"}

    def test_contains_refund_edge_case(self) -> None:
        refunds = [
            transaction
            for transaction in load_marlstone_transactions()
            if transaction["debitCreditMemo"] == "CREDIT"
            and "refund" in transaction["description"].lower()
        ]

        assert refunds

    def test_contains_foreign_currency_edge_case(self) -> None:
        foreign = [
            transaction
            for transaction in load_marlstone_transactions()
            if "originalCurrency" in transaction
        ]

        assert foreign
        for transaction in foreign:
            assert transaction["originalCurrency"] != "EUR"
            assert transaction["originalAmount"] > 0

    def test_posted_transactions_have_utc_timestamps(self) -> None:
        posted = [
            transaction
            for transaction in load_marlstone_transactions()
            if transaction["status"] == "POSTED"
        ]

        for transaction in posted:
            assert transaction["postedTimestamp"].endswith("Z")


class TestTaktwerkFixtures:
    def test_accounts_csv_parses_with_expected_columns(self) -> None:
        header, rows = load_taktwerk_rows("accounts.csv")

        assert header == ["Account Number", "Account Name", "Currency", "IBAN"]
        assert len(rows) >= MINIMUM_ACCOUNTS_PER_BANK

    def test_transactions_csv_has_exact_legacy_header(self) -> None:
        header, _ = load_taktwerk_rows("transactions_export.csv")

        assert header == TAKTWERK_EXPECTED_HEADER

    def test_transaction_count_within_documented_range(self) -> None:
        _, rows = load_taktwerk_rows("transactions_export.csv")

        assert MINIMUM_TRANSACTIONS_PER_BANK <= len(rows) <= MAXIMUM_TRANSACTIONS_PER_BANK

    def test_dates_use_legacy_dotted_format(self) -> None:
        _, rows = load_taktwerk_rows("transactions_export.csv")

        for row in rows:
            assert LEGACY_DATE_PATTERN.fullmatch(row["Booking Date"])
            assert LEGACY_DATE_PATTERN.fullmatch(row["Value Date"])

    def test_amounts_use_decimal_comma_format(self) -> None:
        _, rows = load_taktwerk_rows("transactions_export.csv")

        for row in rows:
            assert DECIMAL_COMMA_AMOUNT_PATTERN.fullmatch(row["Amount"]), row["Amount"]

    def test_contains_thousands_separator_edge_case(self) -> None:
        _, rows = load_taktwerk_rows("transactions_export.csv")

        assert any("." in row["Amount"] for row in rows)

    def test_contains_refund_edge_case(self) -> None:
        _, rows = load_taktwerk_rows("transactions_export.csv")

        refunds = [
            row
            for row in rows
            if "refund" in row["Reference"].lower() and not row["Amount"].startswith("-")
        ]
        assert refunds

    def test_contains_foreign_currency_edge_case(self) -> None:
        _, rows = load_taktwerk_rows("transactions_export.csv")

        assert any(row["Currency"] != "EUR" for row in rows)

    def test_spans_multiple_accounts(self) -> None:
        _, rows = load_taktwerk_rows("transactions_export.csv")

        account_numbers = {row["Account Number"] for row in rows}
        assert len(account_numbers) >= MINIMUM_ACCOUNTS_PER_BANK
