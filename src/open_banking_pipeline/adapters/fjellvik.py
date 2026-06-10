"""Adapter for fjellvik's Berlin-Group-style paginated JSON.

Mapping decisions (ADR-0003): string amounts parse to ``Decimal`` verbatim;
the counterparty is the non-account-holder party, so outflows take
``creditorName`` and inflows take ``debtorName``; ``bankTransactionCode``
is preserved as the raw category for the Phase 3 categorizer.
"""

import json
from datetime import date
from decimal import Decimal
from functools import partial

from open_banking_pipeline.adapters import BankExtract
from open_banking_pipeline.canonical import (
    CanonicalAccount,
    CanonicalTransaction,
    SourceBank,
    TransactionStatus,
    derive_account_id,
    derive_transaction_id,
)
from open_banking_pipeline.ingestion.retry import RetryPolicy, fetch_with_retry
from open_banking_pipeline.mock_banks.fjellvik import (
    ACCOUNTS_PATH,
    FjellvikMockBank,
    transactions_path,
)

FIRST_PAGE_NUMBER = 1


def extract(bank: FjellvikMockBank, retry_policy: RetryPolicy) -> BankExtract:
    """Pull every fjellvik account and transaction into canonical models."""
    accounts_body = fetch_with_retry(partial(bank.request, ACCOUNTS_PATH), retry_policy)
    raw_accounts = json.loads(accounts_body)["accounts"]
    accounts = tuple(map_account(raw_account) for raw_account in raw_accounts)
    transactions: list[CanonicalTransaction] = []
    for account in accounts:
        transactions.extend(_extract_account_transactions(bank, account, retry_policy))
    return BankExtract(accounts=accounts, transactions=tuple(transactions))


def map_account(raw_account: dict) -> CanonicalAccount:
    source_account_id = raw_account["resourceId"]
    return CanonicalAccount(
        account_id=derive_account_id(SourceBank.FJELLVIK, source_account_id),
        source_bank=SourceBank.FJELLVIK,
        source_account_id=source_account_id,
        display_name=raw_account["name"],
        currency=raw_account["currency"],
        iban=raw_account["iban"],
    )


def map_transaction(
    raw_transaction: dict,
    account: CanonicalAccount,
    status: TransactionStatus,
) -> CanonicalTransaction:
    amount = Decimal(raw_transaction["transactionAmount"]["amount"])
    if amount < 0:
        counterparty_name = raw_transaction.get("creditorName")
    else:
        counterparty_name = raw_transaction.get("debtorName")
    source_transaction_id = raw_transaction["transactionId"]
    return CanonicalTransaction(
        transaction_id=derive_transaction_id(
            SourceBank.FJELLVIK, account.source_account_id, source_transaction_id
        ),
        account_id=account.account_id,
        source_bank=SourceBank.FJELLVIK,
        source_account_id=account.source_account_id,
        source_transaction_id=source_transaction_id,
        status=status,
        booking_date=_parse_optional_iso_date(raw_transaction.get("bookingDate")),
        value_date=_parse_optional_iso_date(raw_transaction.get("valueDate")),
        amount=amount,
        currency=raw_transaction["transactionAmount"]["currency"],
        counterparty_name=counterparty_name,
        description=raw_transaction.get("remittanceInformationUnstructured"),
        raw_category=raw_transaction.get("bankTransactionCode"),
    )


def _extract_account_transactions(
    bank: FjellvikMockBank,
    account: CanonicalAccount,
    retry_policy: RetryPolicy,
) -> list[CanonicalTransaction]:
    transactions: list[CanonicalTransaction] = []
    path: str | None = transactions_path(account.source_account_id, FIRST_PAGE_NUMBER)
    while path is not None:
        page_body = fetch_with_retry(partial(bank.request, path), retry_policy)
        account_report = json.loads(page_body)["transactions"]
        for raw_transaction in account_report["booked"]:
            transactions.append(map_transaction(raw_transaction, account, TransactionStatus.BOOKED))
        for raw_transaction in account_report["pending"]:
            transactions.append(
                map_transaction(raw_transaction, account, TransactionStatus.PENDING)
            )
        next_link = account_report["_links"].get("next")
        path = next_link["href"] if next_link else None
    return transactions


def _parse_optional_iso_date(raw_date: str | None) -> date | None:
    return date.fromisoformat(raw_date) if raw_date is not None else None
