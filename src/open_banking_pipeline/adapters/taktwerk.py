"""Adapter for taktwerk's legacy whole-file CSV export.

Mapping decisions (ADR-0003): the export has no transaction ID column, so
identity is content-derived per ADR-0001 (every raw column in header order
plus an occurrence index for byte-identical rows). Truncated downloads are
detected here — a complete export ends with a newline and every row has the
full column count — and surface as ``TruncatedExportError`` for the retry
layer. Header drift is a different failure (schema change) and raises
``ValueError`` instead of being retried.
"""

import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal
from functools import partial

from open_banking_pipeline.adapters import BankExtract
from open_banking_pipeline.canonical import (
    CanonicalAccount,
    CanonicalTransaction,
    SourceBank,
    TransactionStatus,
    derive_account_id,
    derive_content_source_transaction_id,
    derive_transaction_id,
)
from open_banking_pipeline.errors import TruncatedExportError
from open_banking_pipeline.ingestion.retry import RetryPolicy, fetch_with_retry
from open_banking_pipeline.mock_banks.taktwerk import TaktwerkMockBank

CSV_DELIMITER = ";"
EXPECTED_ACCOUNTS_HEADER = ["Account Number", "Account Name", "Currency", "IBAN"]
EXPECTED_TRANSACTIONS_HEADER = [
    "Booking Date",
    "Value Date",
    "Counterparty",
    "Reference",
    "Amount",
    "Currency",
    "Original Amount",
    "Original Currency",
    "Account Number",
]
LEGACY_DATE_FORMAT = "%d.%m.%Y"
LEGACY_AMOUNT_PATTERN = re.compile(r"^-?\d{1,3}(\.\d{3})*,\d{2}$")


def extract(bank: TaktwerkMockBank, retry_policy: RetryPolicy) -> BankExtract:
    """Pull every taktwerk account and transaction into canonical models."""
    accounts = fetch_with_retry(partial(_download_and_parse_accounts, bank), retry_policy)
    fetch_and_parse = partial(_download_and_parse_transactions, bank, accounts)
    transactions = fetch_with_retry(fetch_and_parse, retry_policy)
    return BankExtract(accounts=accounts, transactions=transactions)


def parse_accounts_csv(accounts_text: str) -> tuple[CanonicalAccount, ...]:
    header, rows = _read_csv_rows(accounts_text, EXPECTED_ACCOUNTS_HEADER)
    return tuple(_map_account(dict(zip(header, row, strict=True))) for row in rows)


def parse_transactions_export(
    export_text: str,
    accounts: tuple[CanonicalAccount, ...],
) -> tuple[CanonicalTransaction, ...]:
    """Parse a complete export; raise ``TruncatedExportError`` on a cut-off file."""
    _, rows = _read_csv_rows(export_text, EXPECTED_TRANSACTIONS_HEADER)
    accounts_by_number = {account.source_account_id: account for account in accounts}
    occurrence_counts: dict[tuple[str, ...], int] = {}
    transactions: list[CanonicalTransaction] = []
    for row in rows:
        row_key = tuple(row)
        occurrence_index = occurrence_counts.get(row_key, 0)
        occurrence_counts[row_key] = occurrence_index + 1
        transactions.append(_map_transaction_row(row, occurrence_index, accounts_by_number))
    return tuple(transactions)


def parse_legacy_amount(raw_amount: str) -> Decimal:
    """Parse a dot-thousands, decimal-comma amount like ``-2.450,00``."""
    if not LEGACY_AMOUNT_PATTERN.fullmatch(raw_amount):
        raise ValueError(f"unparseable taktwerk amount: {raw_amount!r}")
    return Decimal(raw_amount.replace(".", "").replace(",", "."))


def _download_and_parse_accounts(bank: TaktwerkMockBank) -> tuple[CanonicalAccount, ...]:
    return parse_accounts_csv(bank.download_accounts_csv())


def _download_and_parse_transactions(
    bank: TaktwerkMockBank,
    accounts: tuple[CanonicalAccount, ...],
) -> tuple[CanonicalTransaction, ...]:
    return parse_transactions_export(bank.download_transactions_export(), accounts)


def _read_csv_rows(
    csv_text: str,
    expected_header: list[str],
) -> tuple[list[str], list[list[str]]]:
    if not csv_text.endswith("\n"):
        raise TruncatedExportError(
            "download does not end with a newline; the file arrived truncated"
        )
    parsed_rows = list(csv.reader(io.StringIO(csv_text), delimiter=CSV_DELIMITER))
    header, rows = parsed_rows[0], parsed_rows[1:]
    if header != expected_header:
        raise ValueError(f"unexpected taktwerk CSV header {header!r}; expected {expected_header!r}")
    for row in rows:
        if len(row) != len(expected_header):
            raise TruncatedExportError(
                f"row has {len(row)} fields, expected {len(expected_header)}; "
                f"the file arrived truncated"
            )
    return header, rows


def _map_account(raw_account: dict[str, str]) -> CanonicalAccount:
    source_account_id = raw_account["Account Number"]
    return CanonicalAccount(
        account_id=derive_account_id(SourceBank.TAKTWERK, source_account_id),
        source_bank=SourceBank.TAKTWERK,
        source_account_id=source_account_id,
        display_name=raw_account["Account Name"],
        currency=raw_account["Currency"],
        iban=raw_account["IBAN"],
    )


def _map_transaction_row(
    row: list[str],
    occurrence_index: int,
    accounts_by_number: dict[str, CanonicalAccount],
) -> CanonicalTransaction:
    fields = dict(zip(EXPECTED_TRANSACTIONS_HEADER, row, strict=True))
    account_number = fields["Account Number"]
    if account_number not in accounts_by_number:
        raise ValueError(f"transaction references unknown taktwerk account: {account_number!r}")
    account = accounts_by_number[account_number]
    source_transaction_id = derive_content_source_transaction_id(row, occurrence_index)
    return CanonicalTransaction(
        transaction_id=derive_transaction_id(
            SourceBank.TAKTWERK, account.source_account_id, source_transaction_id
        ),
        account_id=account.account_id,
        source_bank=SourceBank.TAKTWERK,
        source_account_id=account.source_account_id,
        source_transaction_id=source_transaction_id,
        status=TransactionStatus.BOOKED,
        booking_date=_parse_legacy_date(fields["Booking Date"]),
        value_date=_parse_legacy_date(fields["Value Date"]),
        amount=parse_legacy_amount(fields["Amount"]),
        currency=fields["Currency"],
        counterparty_name=fields["Counterparty"],
        description=fields["Reference"],
        raw_category=None,
    )


def _parse_legacy_date(raw_date: str) -> date:
    return datetime.strptime(raw_date, LEGACY_DATE_FORMAT).date()
