"""Round-trip tests: every fixture transaction must land canonically."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from open_banking_pipeline.adapters import BankExtract
from open_banking_pipeline.adapters import fjellvik as fjellvik_adapter
from open_banking_pipeline.adapters import marlstone as marlstone_adapter
from open_banking_pipeline.adapters import taktwerk as taktwerk_adapter
from open_banking_pipeline.canonical import (
    CanonicalTransaction,
    SourceBank,
    TransactionCategory,
    TransactionStatus,
    derive_content_source_transaction_id,
)
from open_banking_pipeline.errors import TruncatedExportError
from open_banking_pipeline.ingestion.retry import RetryPolicy
from open_banking_pipeline.mock_banks.failures import PlannedFailures
from open_banking_pipeline.mock_banks.fjellvik import FjellvikMockBank
from open_banking_pipeline.mock_banks.marlstone import MarlstoneMockBank
from open_banking_pipeline.mock_banks.taktwerk import TaktwerkMockBank

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"

FJELLVIK_TRANSACTION_COUNT = 15
MARLSTONE_TRANSACTION_COUNT = 16
TAKTWERK_TRANSACTION_COUNT = 15
ACCOUNTS_PER_BANK = 2

TAKTWERK_FIRST_EXPORT_ROW = [
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


def sleep_immediately(_seconds: float) -> None:
    """No-op sleeper keeping retry-exercising tests instant."""


class AccountsTruncatingTaktwerkBank(TaktwerkMockBank):
    """Truncates the first accounts download, mirroring the transactions failure shape."""

    def __init__(self, fixtures_dir: Path) -> None:
        super().__init__(fixtures_dir)
        self._accounts_download_count = 0

    def download_accounts_csv(self) -> str:
        full_text = super().download_accounts_csv()
        self._accounts_download_count += 1
        if self._accounts_download_count == 1:
            return full_text[:-1]
        return full_text


NO_SLEEP_POLICY = RetryPolicy(sleep=sleep_immediately)


def transactions_by_source_id(extract: BankExtract) -> dict[str, CanonicalTransaction]:
    return {transaction.source_transaction_id: transaction for transaction in extract.transactions}


@pytest.fixture(scope="module")
def fjellvik_extract() -> BankExtract:
    return fjellvik_adapter.extract(FjellvikMockBank(FIXTURES_DIR), NO_SLEEP_POLICY)


@pytest.fixture(scope="module")
def marlstone_extract() -> BankExtract:
    return marlstone_adapter.extract(MarlstoneMockBank(FIXTURES_DIR), NO_SLEEP_POLICY)


@pytest.fixture(scope="module")
def taktwerk_extract() -> BankExtract:
    return taktwerk_adapter.extract(TaktwerkMockBank(FIXTURES_DIR), NO_SLEEP_POLICY)


class TestFjellvikAdapter:
    def test_every_fixture_transaction_lands_canonically(
        self, fjellvik_extract: BankExtract
    ) -> None:
        assert len(fjellvik_extract.accounts) == ACCOUNTS_PER_BANK
        assert len(fjellvik_extract.transactions) == FJELLVIK_TRANSACTION_COUNT
        unique_ids = {transaction.transaction_id for transaction in fjellvik_extract.transactions}
        assert len(unique_ids) == FJELLVIK_TRANSACTION_COUNT

    def test_account_maps_resource_id_name_and_iban(self, fjellvik_extract: BankExtract) -> None:
        account = fjellvik_extract.accounts[0]

        assert account.account_id == "fjellvik:FV-ACC-001"
        assert account.source_bank is SourceBank.FJELLVIK
        assert account.display_name == "Main Current Account"
        assert account.currency == "EUR"
        assert account.iban == "DE89370400440532013000"

    def test_card_payment_maps_amount_dates_and_counterparty(
        self, fjellvik_extract: BankExtract
    ) -> None:
        transaction = transactions_by_source_id(fjellvik_extract)["FV-TX-1001"]

        assert transaction.amount == Decimal("-23.40")
        assert transaction.currency == "EUR"
        assert transaction.status is TransactionStatus.BOOKED
        assert transaction.booking_date == date(2026, 5, 2)
        assert transaction.value_date == date(2026, 5, 3)
        assert transaction.counterparty_name == "Greenfield Grocers"
        assert transaction.description == "Card payment groceries"
        assert transaction.raw_category == "PMNT-CCRD-POSD"
        assert transaction.category is TransactionCategory.UNCATEGORIZED

    def test_salary_inflow_takes_debtor_as_counterparty(
        self, fjellvik_extract: BankExtract
    ) -> None:
        transaction = transactions_by_source_id(fjellvik_extract)["FV-TX-1003"]

        assert transaction.amount == Decimal("2450.00")
        assert transaction.counterparty_name == "Brightline Consulting GmbH"

    def test_refund_lands_as_positive_inflow(self, fjellvik_extract: BankExtract) -> None:
        transaction = transactions_by_source_id(fjellvik_extract)["FV-TX-1006"]

        assert transaction.amount == Decimal("34.50")
        assert transaction.counterparty_name == "Greenfield Grocers"

    def test_fx_transaction_keeps_account_currency_amount(
        self, fjellvik_extract: BankExtract
    ) -> None:
        transaction = transactions_by_source_id(fjellvik_extract)["FV-TX-1005"]

        assert transaction.amount == Decimal("-119.37")
        assert transaction.currency == "EUR"

    def test_pending_transaction_has_no_booking_date(self, fjellvik_extract: BankExtract) -> None:
        transaction = transactions_by_source_id(fjellvik_extract)["FV-TX-1010"]

        assert transaction.status is TransactionStatus.PENDING
        assert transaction.booking_date is None
        assert transaction.value_date == date(2026, 6, 1)

    def test_rate_limited_pagination_recovers_and_loses_nothing(self) -> None:
        bank = FjellvikMockBank(
            FIXTURES_DIR,
            planned_failures=PlannedFailures(failing_request_indexes=frozenset({2})),
        )

        extract = fjellvik_adapter.extract(bank, NO_SLEEP_POLICY)

        assert len(extract.transactions) == FJELLVIK_TRANSACTION_COUNT
        unique_ids = {transaction.transaction_id for transaction in extract.transactions}
        assert len(unique_ids) == FJELLVIK_TRANSACTION_COUNT


class TestMarlstoneAdapter:
    def test_every_fixture_transaction_lands_canonically(
        self, marlstone_extract: BankExtract
    ) -> None:
        assert len(marlstone_extract.accounts) == ACCOUNTS_PER_BANK
        assert len(marlstone_extract.transactions) == MARLSTONE_TRANSACTION_COUNT

    def test_account_maps_nickname_and_has_no_iban(self, marlstone_extract: BankExtract) -> None:
        account = marlstone_extract.accounts[0]

        assert account.account_id == "marlstone:MS-330011"
        assert account.display_name == "Everyday Checking"
        assert account.currency == "EUR"
        assert account.iban is None

    def test_debit_becomes_negative_amount(self, marlstone_extract: BankExtract) -> None:
        transaction = transactions_by_source_id(marlstone_extract)["MS-TXN-88001"]

        assert transaction.amount == Decimal("-41.27")
        assert transaction.status is TransactionStatus.BOOKED
        assert transaction.booking_date == date(2026, 5, 2)
        assert transaction.value_date == date(2026, 5, 1)
        assert transaction.raw_category == "Groceries"
        assert transaction.counterparty_name is None
        assert transaction.description == "POS PURCHASE - GREENFIELD GROCERS BERLIN"

    def test_credit_refund_becomes_positive_amount(self, marlstone_extract: BankExtract) -> None:
        transaction = transactions_by_source_id(marlstone_extract)["MS-TXN-88006"]

        assert transaction.amount == Decimal("89.99")

    def test_fx_transaction_keeps_account_currency_amount(
        self, marlstone_extract: BankExtract
    ) -> None:
        transaction = transactions_by_source_id(marlstone_extract)["MS-TXN-88005"]

        assert transaction.amount == Decimal("-168.74")
        assert transaction.currency == "EUR"

    def test_pending_transaction_has_no_booking_date(self, marlstone_extract: BankExtract) -> None:
        transaction = transactions_by_source_id(marlstone_extract)["MS-TXN-88012"]

        assert transaction.status is TransactionStatus.PENDING
        assert transaction.booking_date is None
        assert transaction.value_date == date(2026, 6, 1)

    def test_unknown_debit_credit_memo_is_rejected(self, marlstone_extract: BankExtract) -> None:
        raw_transaction = {
            "transactionId": "MS-TXN-00001",
            "accountId": "MS-330011",
            "transactionTimestamp": "2026-06-01T00:00:00Z",
            "description": "BROKEN MEMO",
            "debitCreditMemo": "REVERSAL",
            "amount": Decimal("1.00"),
            "status": "POSTED",
            "category": "Fees",
        }

        with pytest.raises(ValueError, match="debitCreditMemo"):
            marlstone_adapter.map_transaction(raw_transaction, marlstone_extract.accounts[0])

    def test_non_positive_source_amount_is_rejected(self, marlstone_extract: BankExtract) -> None:
        raw_transaction = {
            "transactionId": "MS-TXN-00002",
            "accountId": "MS-330011",
            "transactionTimestamp": "2026-06-01T00:00:00Z",
            "description": "ALREADY SIGNED",
            "debitCreditMemo": "DEBIT",
            "amount": Decimal("-1.00"),
            "status": "POSTED",
            "category": "Fees",
        }

        with pytest.raises(ValueError, match="unsigned"):
            marlstone_adapter.map_transaction(raw_transaction, marlstone_extract.accounts[0])

    def test_zero_source_amount_is_rejected(self, marlstone_extract: BankExtract) -> None:
        raw_transaction = {
            "transactionId": "MS-TXN-00004",
            "accountId": "MS-330011",
            "transactionTimestamp": "2026-06-01T00:00:00Z",
            "description": "ZERO AMOUNT",
            "debitCreditMemo": "DEBIT",
            "amount": Decimal("0.00"),
            "status": "POSTED",
            "category": "Fees",
        }

        with pytest.raises(ValueError, match="unsigned"):
            marlstone_adapter.map_transaction(raw_transaction, marlstone_extract.accounts[0])

    def test_known_account_with_zero_transactions_lands_empty(
        self, marlstone_fixtures_with_empty_account: Path
    ) -> None:
        bank = MarlstoneMockBank(marlstone_fixtures_with_empty_account)

        extract = marlstone_adapter.extract(bank, NO_SLEEP_POLICY)

        assert len(extract.accounts) == 2
        assert [transaction.source_account_id for transaction in extract.transactions] == [
            "MS-550033"
        ]

    def test_unknown_status_is_rejected(self, marlstone_extract: BankExtract) -> None:
        raw_transaction = {
            "transactionId": "MS-TXN-00003",
            "accountId": "MS-330011",
            "transactionTimestamp": "2026-06-01T00:00:00Z",
            "description": "BROKEN STATUS",
            "debitCreditMemo": "DEBIT",
            "amount": Decimal("1.00"),
            "status": "VOIDED",
            "category": "Fees",
        }

        with pytest.raises(ValueError, match="status"):
            marlstone_adapter.map_transaction(raw_transaction, marlstone_extract.accounts[0])


class TestTaktwerkAdapter:
    def test_every_fixture_transaction_lands_canonically(
        self, taktwerk_extract: BankExtract
    ) -> None:
        assert len(taktwerk_extract.accounts) == ACCOUNTS_PER_BANK
        assert len(taktwerk_extract.transactions) == TAKTWERK_TRANSACTION_COUNT
        unique_ids = {transaction.transaction_id for transaction in taktwerk_extract.transactions}
        assert len(unique_ids) == TAKTWERK_TRANSACTION_COUNT

    def test_account_maps_legacy_columns(self, taktwerk_extract: BankExtract) -> None:
        account = taktwerk_extract.accounts[0]

        assert account.account_id == "taktwerk:TW-7701"
        assert account.display_name == "Privatkonto Classic"
        assert account.currency == "EUR"
        assert account.iban == "DE45500105175407324931"

    def test_decimal_comma_amount_and_dotted_dates_parse(
        self, taktwerk_extract: BankExtract
    ) -> None:
        transaction = taktwerk_extract.transactions[0]

        assert transaction.amount == Decimal("-31.86")
        assert transaction.booking_date == date(2026, 5, 2)
        assert transaction.value_date == date(2026, 5, 3)
        assert transaction.counterparty_name == "GREENFIELD GROCERS"
        assert transaction.description == "Kartenzahlung Lebensmittel"
        assert transaction.raw_category is None
        assert transaction.status is TransactionStatus.BOOKED

    def test_thousands_separator_amount_parses(self, taktwerk_extract: BankExtract) -> None:
        amounts = {transaction.amount for transaction in taktwerk_extract.transactions}

        assert Decimal("2450.00") in amounts
        assert Decimal("-1234.56") in amounts
        assert Decimal("-2150.75") in amounts

    def test_fx_transaction_keeps_account_currency_amount(
        self, taktwerk_extract: BankExtract
    ) -> None:
        fx_transactions = [
            transaction
            for transaction in taktwerk_extract.transactions
            if transaction.amount == Decimal("-379.78")
        ]

        assert len(fx_transactions) == 1
        assert fx_transactions[0].currency == "EUR"

    def test_transactions_span_both_accounts(self, taktwerk_extract: BankExtract) -> None:
        account_ids = {transaction.account_id for transaction in taktwerk_extract.transactions}

        assert account_ids == {"taktwerk:TW-7701", "taktwerk:TW-7702"}

    def test_first_row_uses_documented_content_derived_id(
        self, taktwerk_extract: BankExtract
    ) -> None:
        expected = derive_content_source_transaction_id(TAKTWERK_FIRST_EXPORT_ROW, 0)

        assert taktwerk_extract.transactions[0].source_transaction_id == expected

    def test_replayed_extraction_derives_identical_ids(self, taktwerk_extract: BankExtract) -> None:
        replayed = taktwerk_adapter.extract(TaktwerkMockBank(FIXTURES_DIR), NO_SLEEP_POLICY)

        first_ids = [transaction.transaction_id for transaction in taktwerk_extract.transactions]
        replayed_ids = [transaction.transaction_id for transaction in replayed.transactions]
        assert first_ids == replayed_ids

    def test_truncated_download_is_detected_and_retried(self) -> None:
        bank = TaktwerkMockBank(
            FIXTURES_DIR,
            planned_failures=PlannedFailures(failing_request_indexes=frozenset({0})),
        )

        extract = taktwerk_adapter.extract(bank, NO_SLEEP_POLICY)

        assert len(extract.transactions) == TAKTWERK_TRANSACTION_COUNT

    def test_truncated_accounts_download_is_detected_and_retried(self) -> None:
        bank = AccountsTruncatingTaktwerkBank(FIXTURES_DIR)

        extract = taktwerk_adapter.extract(bank, NO_SLEEP_POLICY)

        assert len(extract.accounts) == ACCOUNTS_PER_BANK
        assert len(extract.transactions) == TAKTWERK_TRANSACTION_COUNT

    def test_export_missing_only_the_final_newline_is_detected_as_truncated(
        self, taktwerk_extract: BankExtract
    ) -> None:
        full_text = (FIXTURES_DIR / "taktwerk" / "transactions_export.csv").read_text()
        cut_at_row_boundary = full_text[:-1]

        with pytest.raises(TruncatedExportError):
            taktwerk_adapter.parse_transactions_export(
                cut_at_row_boundary, taktwerk_extract.accounts
            )

    def test_export_with_short_row_but_final_newline_is_detected_as_truncated(
        self, taktwerk_extract: BankExtract
    ) -> None:
        full_text = (FIXTURES_DIR / "taktwerk" / "transactions_export.csv").read_text()
        header_line = full_text.splitlines()[0]
        short_row = "02.05.2026;03.05.2026;GREENFIELD GROCERS;Kartenzahlung"
        export_with_short_row = f"{header_line}\n{short_row}\n"

        with pytest.raises(TruncatedExportError):
            taktwerk_adapter.parse_transactions_export(
                export_with_short_row, taktwerk_extract.accounts
            )

    def test_persistent_truncation_fails_loudly(self) -> None:
        bank = TaktwerkMockBank(
            FIXTURES_DIR,
            planned_failures=PlannedFailures(failing_request_indexes=frozenset(range(10))),
        )

        with pytest.raises(TruncatedExportError):
            taktwerk_adapter.extract(bank, NO_SLEEP_POLICY)

    def test_header_drift_is_rejected(self, taktwerk_extract: BankExtract) -> None:
        drifted = "Buchungstag;Wert;Partner\n01.01.2026;01.01.2026;X\n"

        with pytest.raises(ValueError, match="header"):
            taktwerk_adapter.parse_transactions_export(drifted, taktwerk_extract.accounts)
