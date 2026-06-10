"""Tests for the three in-process mock bank APIs and their interaction shapes."""

import json
from pathlib import Path

import pytest

from open_banking_pipeline.errors import RateLimitError
from open_banking_pipeline.mock_banks.failures import PlannedFailures
from open_banking_pipeline.mock_banks.fjellvik import FjellvikMockBank
from open_banking_pipeline.mock_banks.marlstone import MarlstoneMockBank
from open_banking_pipeline.mock_banks.taktwerk import TaktwerkMockBank

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"

FJELLVIK_FIRST_ACCOUNT_TRANSACTION_COUNT = 11
FJELLVIK_SECOND_ACCOUNT_TRANSACTION_COUNT = 4
MARLSTONE_FIRST_ACCOUNT_TRANSACTION_COUNT = 13
MARLSTONE_SECOND_ACCOUNT_TRANSACTION_COUNT = 3


def collect_fjellvik_pages(bank: FjellvikMockBank, resource_id: str) -> list[dict]:
    pages = []
    path: str | None = f"/v1/accounts/{resource_id}/transactions?page=1"
    while path is not None:
        page = json.loads(bank.request(path))
        pages.append(page)
        next_link = page["transactions"]["_links"].get("next")
        path = next_link["href"] if next_link else None
    return pages


def collect_marlstone_pages(bank: MarlstoneMockBank, account_id: str) -> list[dict]:
    pages = []
    base_path = f"/fdx/v6/accounts/{account_id}/transactions"
    path = base_path
    while True:
        page = json.loads(bank.request(path))
        pages.append(page)
        next_offset = page["page"]["nextOffset"]
        if next_offset is None:
            return pages
        path = f"{base_path}?offset={next_offset}"


class TestFjellvikMockBank:
    def test_accounts_body_matches_fixture(self) -> None:
        bank = FjellvikMockBank(FIXTURES_DIR)

        body = json.loads(bank.request("/v1/accounts"))

        fixture = json.loads((FIXTURES_DIR / "fjellvik" / "accounts.json").read_text())
        assert body == fixture

    def test_first_transactions_page_is_full_and_links_to_next(self) -> None:
        bank = FjellvikMockBank(FIXTURES_DIR)

        page = json.loads(bank.request("/v1/accounts/FV-ACC-001/transactions?page=1"))

        assert len(page["transactions"]["booked"]) == 4
        assert page["transactions"]["pending"] == []
        assert page["transactions"]["_links"]["next"]["href"] == (
            "/v1/accounts/FV-ACC-001/transactions?page=2"
        )

    def test_first_account_paginates_over_three_pages_without_duplicates(self) -> None:
        bank = FjellvikMockBank(FIXTURES_DIR)

        pages = collect_fjellvik_pages(bank, "FV-ACC-001")

        transaction_ids = [
            entry["transactionId"]
            for page in pages
            for status in ("booked", "pending")
            for entry in page["transactions"][status]
        ]
        assert len(pages) == 3
        assert len(transaction_ids) == FJELLVIK_FIRST_ACCOUNT_TRANSACTION_COUNT
        assert len(set(transaction_ids)) == FJELLVIK_FIRST_ACCOUNT_TRANSACTION_COUNT

    def test_pending_transactions_arrive_on_the_last_page(self) -> None:
        bank = FjellvikMockBank(FIXTURES_DIR)

        pages = collect_fjellvik_pages(bank, "FV-ACC-001")

        assert [len(page["transactions"]["pending"]) for page in pages] == [0, 0, 2]

    def test_second_account_fits_one_page_with_no_next_link(self) -> None:
        bank = FjellvikMockBank(FIXTURES_DIR)

        pages = collect_fjellvik_pages(bank, "FV-ACC-002")

        assert len(pages) == 1
        assert "next" not in pages[0]["transactions"]["_links"]

    def test_planned_failure_raises_rate_limit_with_retry_after(self) -> None:
        bank = FjellvikMockBank(
            FIXTURES_DIR,
            planned_failures=PlannedFailures(failing_request_indexes=frozenset({0})),
        )

        with pytest.raises(RateLimitError) as raised:
            bank.request("/v1/accounts")

        assert raised.value.retry_after_seconds > 0

    def test_retrying_the_failed_request_succeeds(self) -> None:
        bank = FjellvikMockBank(
            FIXTURES_DIR,
            planned_failures=PlannedFailures(failing_request_indexes=frozenset({0})),
        )

        with pytest.raises(RateLimitError):
            bank.request("/v1/accounts")
        body = json.loads(bank.request("/v1/accounts"))

        assert body["accounts"]

    def test_unknown_path_is_rejected(self) -> None:
        bank = FjellvikMockBank(FIXTURES_DIR)

        with pytest.raises(ValueError, match="path"):
            bank.request("/v1/balances")

    def test_unknown_account_is_rejected(self) -> None:
        bank = FjellvikMockBank(FIXTURES_DIR)

        with pytest.raises(ValueError, match="FV-ACC-999"):
            bank.request("/v1/accounts/FV-ACC-999/transactions?page=1")

    def test_page_past_the_end_is_rejected(self) -> None:
        bank = FjellvikMockBank(FIXTURES_DIR)

        with pytest.raises(ValueError, match="page"):
            bank.request("/v1/accounts/FV-ACC-001/transactions?page=9")


class TestMarlstoneMockBank:
    def test_accounts_body_matches_fixture(self) -> None:
        bank = MarlstoneMockBank(FIXTURES_DIR)

        body = json.loads(bank.request("/fdx/v6/accounts"))

        fixture = json.loads((FIXTURES_DIR / "marlstone" / "accounts.json").read_text())
        assert body == fixture

    def test_first_page_carries_an_opaque_next_offset(self) -> None:
        bank = MarlstoneMockBank(FIXTURES_DIR)

        page = json.loads(bank.request("/fdx/v6/accounts/MS-330011/transactions"))

        assert len(page["transactions"]) == 6
        assert isinstance(page["page"]["nextOffset"], str)
        assert page["page"]["total"] == MARLSTONE_FIRST_ACCOUNT_TRANSACTION_COUNT

    def test_cursor_pagination_yields_every_transaction_exactly_once(self) -> None:
        bank = MarlstoneMockBank(FIXTURES_DIR)

        pages = collect_marlstone_pages(bank, "MS-330011")

        transaction_ids = [
            entry["depositTransaction"]["transactionId"]
            for page in pages
            for entry in page["transactions"]
        ]
        assert len(pages) == 3
        assert len(transaction_ids) == MARLSTONE_FIRST_ACCOUNT_TRANSACTION_COUNT
        assert len(set(transaction_ids)) == MARLSTONE_FIRST_ACCOUNT_TRANSACTION_COUNT

    def test_small_account_fits_one_page(self) -> None:
        bank = MarlstoneMockBank(FIXTURES_DIR)

        pages = collect_marlstone_pages(bank, "MS-440022")

        assert len(pages) == 1
        assert len(pages[0]["transactions"]) == MARLSTONE_SECOND_ACCOUNT_TRANSACTION_COUNT
        assert pages[0]["page"]["nextOffset"] is None

    def test_amounts_stay_numeric_in_the_page_body(self) -> None:
        bank = MarlstoneMockBank(FIXTURES_DIR)

        page = json.loads(bank.request("/fdx/v6/accounts/MS-330011/transactions"))

        first_amount = page["transactions"][0]["depositTransaction"]["amount"]
        assert isinstance(first_amount, float)

    def test_unknown_account_is_rejected(self) -> None:
        bank = MarlstoneMockBank(FIXTURES_DIR)

        with pytest.raises(ValueError, match="MS-999999"):
            bank.request("/fdx/v6/accounts/MS-999999/transactions")

    def test_invalid_cursor_is_rejected(self) -> None:
        bank = MarlstoneMockBank(FIXTURES_DIR)

        with pytest.raises(ValueError, match="offset"):
            bank.request("/fdx/v6/accounts/MS-330011/transactions?offset=garbage")

    def test_unknown_path_is_rejected(self) -> None:
        bank = MarlstoneMockBank(FIXTURES_DIR)

        with pytest.raises(ValueError, match="path"):
            bank.request("/fdx/v6/customers")


class TestTaktwerkMockBank:
    def test_accounts_download_matches_fixture_bytes(self) -> None:
        bank = TaktwerkMockBank(FIXTURES_DIR)

        text = bank.download_accounts_csv()

        assert text == (FIXTURES_DIR / "taktwerk" / "accounts.csv").read_text()

    def test_transactions_download_matches_fixture_bytes(self) -> None:
        bank = TaktwerkMockBank(FIXTURES_DIR)

        text = bank.download_transactions_export()

        assert text == (FIXTURES_DIR / "taktwerk" / "transactions_export.csv").read_text()

    def test_planned_failure_returns_silently_truncated_file(self) -> None:
        bank = TaktwerkMockBank(
            FIXTURES_DIR,
            planned_failures=PlannedFailures(failing_request_indexes=frozenset({0})),
        )
        full_text = (FIXTURES_DIR / "taktwerk" / "transactions_export.csv").read_text()

        truncated = bank.download_transactions_export()

        assert len(truncated) < len(full_text)
        assert not truncated.endswith("\n")
        assert full_text.startswith(truncated)

    def test_retrying_the_failed_download_returns_the_full_file(self) -> None:
        bank = TaktwerkMockBank(
            FIXTURES_DIR,
            planned_failures=PlannedFailures(failing_request_indexes=frozenset({0})),
        )

        first = bank.download_transactions_export()
        second = bank.download_transactions_export()

        assert len(first) < len(second)
        assert second == (FIXTURES_DIR / "taktwerk" / "transactions_export.csv").read_text()
