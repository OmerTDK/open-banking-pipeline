"""Shared fixtures for tests that need purpose-built bank fixture sets."""

import json
from pathlib import Path

import pytest

MARLSTONE_EMPTY_ACCOUNT_ID = "MS-660044"
MARLSTONE_ACTIVE_ACCOUNT_ID = "MS-550033"

MARLSTONE_ACCOUNTS_WITH_EMPTY_ACCOUNT = {
    "accounts": [
        {
            "depositAccount": {
                "accountId": MARLSTONE_ACTIVE_ACCOUNT_ID,
                "accountType": "CHECKING",
                "nickname": "Active Checking",
                "status": "OPEN",
                "currency": {"currencyCode": "EUR"},
            }
        },
        {
            "depositAccount": {
                "accountId": MARLSTONE_EMPTY_ACCOUNT_ID,
                "accountType": "SAVINGS",
                "nickname": "Dormant Savings",
                "status": "OPEN",
                "currency": {"currencyCode": "EUR"},
            }
        },
    ]
}
MARLSTONE_TRANSACTIONS_FOR_ACTIVE_ACCOUNT_ONLY = {
    "transactions": [
        {
            "depositTransaction": {
                "transactionId": "MS-TXN-99001",
                "accountId": MARLSTONE_ACTIVE_ACCOUNT_ID,
                "postedTimestamp": "2026-05-02T09:14:00Z",
                "transactionTimestamp": "2026-05-01T18:22:00Z",
                "description": "POS PURCHASE - GREENFIELD GROCERS BERLIN",
                "debitCreditMemo": "DEBIT",
                "amount": 12.5,
                "status": "POSTED",
                "category": "Groceries",
            }
        }
    ]
}


@pytest.fixture
def marlstone_fixtures_with_empty_account(tmp_path: Path) -> Path:
    """Fixture set where one known marlstone account has zero transactions."""
    bank_dir = tmp_path / "marlstone"
    bank_dir.mkdir()
    (bank_dir / "accounts.json").write_text(
        json.dumps(MARLSTONE_ACCOUNTS_WITH_EMPTY_ACCOUNT), encoding="utf-8"
    )
    (bank_dir / "transactions.json").write_text(
        json.dumps(MARLSTONE_TRANSACTIONS_FOR_ACTIVE_ACCOUNT_ONLY), encoding="utf-8"
    )
    return tmp_path
