"""Berlin-Group-style mock bank: page-linked JSON with planned 429 responses.

Interaction shape (ADR-0003): one GET-like ``request(path)`` surface, a
per-account transactions endpoint paginated via ``_links.next.href``, and
rate limiting modeled as ``RateLimitError`` (HTTP 429 + Retry-After).
"""

import json
import math
import re
from pathlib import Path

from open_banking_pipeline.errors import RateLimitError
from open_banking_pipeline.mock_banks.failures import PlannedFailures

ACCOUNTS_PATH = "/v1/accounts"
TRANSACTIONS_PATH_PATTERN = re.compile(
    r"^/v1/accounts/(?P<resource_id>[A-Za-z0-9-]+)/transactions\?page=(?P<page_number>\d+)$"
)
DEFAULT_PAGE_SIZE = 4
RETRY_AFTER_SECONDS = 0.1


def transactions_path(resource_id: str, page_number: int) -> str:
    return f"/v1/accounts/{resource_id}/transactions?page={page_number}"


class FjellvikMockBank:
    """Serves the fjellvik fixtures through a Berlin-Group-style JSON API."""

    def __init__(
        self,
        fixtures_dir: Path,
        planned_failures: PlannedFailures | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> None:
        self._bank_dir = fixtures_dir / "fjellvik"
        self._planned_failures = planned_failures or PlannedFailures.never()
        self._page_size = page_size
        self._request_index = 0

    def request(self, path: str) -> str:
        """Return the JSON body for ``path``, or raise ``RateLimitError`` (429)."""
        self._consume_request_or_rate_limit()
        if path == ACCOUNTS_PATH:
            return (self._bank_dir / "accounts.json").read_text(encoding="utf-8")
        match = TRANSACTIONS_PATH_PATTERN.fullmatch(path)
        if match is None:
            raise ValueError(f"unknown fjellvik path: {path!r}")
        return self._transactions_page(match.group("resource_id"), int(match.group("page_number")))

    def _consume_request_or_rate_limit(self) -> None:
        request_index = self._request_index
        self._request_index += 1
        if self._planned_failures.should_fail(request_index):
            raise RateLimitError(retry_after_seconds=RETRY_AFTER_SECONDS)

    def _transactions_page(self, resource_id: str, page_number: int) -> str:
        fixture_path = self._bank_dir / f"transactions_{resource_id}.json"
        if not fixture_path.exists():
            raise ValueError(f"unknown fjellvik account: {resource_id!r}")
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        tagged_entries = [
            (status, entry)
            for status in ("booked", "pending")
            for entry in fixture["transactions"][status]
        ]
        page_count = math.ceil(len(tagged_entries) / self._page_size)
        if page_number < 1 or page_number > page_count:
            raise ValueError(
                f"page {page_number} out of range for {resource_id!r} (1..{page_count})"
            )
        page_start = (page_number - 1) * self._page_size
        page_entries = tagged_entries[page_start : page_start + self._page_size]
        links: dict[str, dict[str, str]] = {
            "self": {"href": transactions_path(resource_id, page_number)}
        }
        if page_number < page_count:
            links["next"] = {"href": transactions_path(resource_id, page_number + 1)}
        body = {
            "account": fixture["account"],
            "transactions": {
                "booked": [entry for status, entry in page_entries if status == "booked"],
                "pending": [entry for status, entry in page_entries if status == "pending"],
                "_links": links,
            },
        }
        return json.dumps(body)
