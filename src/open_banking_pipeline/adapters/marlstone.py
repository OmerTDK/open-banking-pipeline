"""Adapter for marlstone's FDX-style cursor-paginated JSON.

Mapping decisions (ADR-0003): JSON numbers parse as ``Decimal`` via
``parse_float`` so float artifacts never reach the canonical layer; the
unsigned amount is signed from ``debitCreditMemo`` (DEBIT = outflow);
``postedTimestamp`` (UTC) dates the booking, ``transactionTimestamp`` the
value date; FDX has no counterparty field, so it stays ``None`` and the
description carries what the bank knows.
"""

import json
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
    derive_transaction_id,
)
from open_banking_pipeline.ingestion.retry import RetryPolicy, fetch_with_retry
from open_banking_pipeline.mock_banks.marlstone import ACCOUNTS_PATH, MarlstoneMockBank

TRANSACTION_STATUS_BY_FDX_STATUS = {
    "POSTED": TransactionStatus.BOOKED,
    "PENDING": TransactionStatus.PENDING,
}
AMOUNT_SIGN_BY_MEMO = {"DEBIT": Decimal(-1), "CREDIT": Decimal(1)}


def extract(bank: MarlstoneMockBank, retry_policy: RetryPolicy) -> BankExtract:
    """Pull every marlstone account and transaction into canonical models."""
    accounts_body = fetch_with_retry(partial(bank.request, ACCOUNTS_PATH), retry_policy)
    raw_accounts = json.loads(accounts_body)["accounts"]
    accounts = tuple(map_account(entry["depositAccount"]) for entry in raw_accounts)
    transactions: list[CanonicalTransaction] = []
    for account in accounts:
        transactions.extend(_extract_account_transactions(bank, account, retry_policy))
    return BankExtract(accounts=accounts, transactions=tuple(transactions))


def map_account(raw_account: dict) -> CanonicalAccount:
    source_account_id = raw_account["accountId"]
    return CanonicalAccount(
        account_id=derive_account_id(SourceBank.MARLSTONE, source_account_id),
        source_bank=SourceBank.MARLSTONE,
        source_account_id=source_account_id,
        display_name=raw_account["nickname"],
        currency=raw_account["currency"]["currencyCode"],
        iban=None,
    )


def map_transaction(raw_transaction: dict, account: CanonicalAccount) -> CanonicalTransaction:
    raw_status = raw_transaction["status"]
    if raw_status not in TRANSACTION_STATUS_BY_FDX_STATUS:
        raise ValueError(f"unknown marlstone transaction status: {raw_status!r}")
    memo = raw_transaction["debitCreditMemo"]
    if memo not in AMOUNT_SIGN_BY_MEMO:
        raise ValueError(f"unknown marlstone debitCreditMemo: {memo!r}")
    unsigned_amount = raw_transaction["amount"]
    if unsigned_amount <= 0:
        raise ValueError(
            f"marlstone amounts are unsigned and must be positive, got {unsigned_amount}"
        )
    source_transaction_id = raw_transaction["transactionId"]
    return CanonicalTransaction(
        transaction_id=derive_transaction_id(
            SourceBank.MARLSTONE, account.source_account_id, source_transaction_id
        ),
        account_id=account.account_id,
        source_bank=SourceBank.MARLSTONE,
        source_account_id=account.source_account_id,
        source_transaction_id=source_transaction_id,
        status=TRANSACTION_STATUS_BY_FDX_STATUS[raw_status],
        booking_date=_parse_optional_utc_date(raw_transaction.get("postedTimestamp")),
        value_date=_parse_optional_utc_date(raw_transaction.get("transactionTimestamp")),
        amount=AMOUNT_SIGN_BY_MEMO[memo] * unsigned_amount,
        currency=account.currency,
        counterparty_name=None,
        description=raw_transaction.get("description"),
        raw_category=raw_transaction.get("category"),
    )


def _extract_account_transactions(
    bank: MarlstoneMockBank,
    account: CanonicalAccount,
    retry_policy: RetryPolicy,
) -> list[CanonicalTransaction]:
    transactions: list[CanonicalTransaction] = []
    base_path = f"/fdx/v6/accounts/{account.source_account_id}/transactions"
    path = base_path
    while True:
        page_body = fetch_with_retry(partial(bank.request, path), retry_policy)
        page = json.loads(page_body, parse_float=Decimal)
        for entry in page["transactions"]:
            transactions.append(map_transaction(entry["depositTransaction"], account))
        next_offset = page["page"]["nextOffset"]
        if next_offset is None:
            return transactions
        path = f"{base_path}?offset={next_offset}"


def _parse_optional_utc_date(raw_timestamp: str | None) -> date | None:
    if raw_timestamp is None:
        return None
    return datetime.fromisoformat(raw_timestamp).date()
