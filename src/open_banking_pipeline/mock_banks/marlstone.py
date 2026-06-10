"""FDX-style mock bank: cursor-paginated JSON over one request surface.

Interaction shape (ADR-0003): per-account transactions endpoint where each
page carries an opaque ``page.nextOffset`` cursor; the caller echoes it back
as an ``offset`` query parameter until the cursor is null.
"""

import json
import re
from pathlib import Path

ACCOUNTS_PATH = "/fdx/v6/accounts"
TRANSACTIONS_PATH_PATTERN = re.compile(
    r"^/fdx/v6/accounts/(?P<account_id>[A-Za-z0-9-]+)/transactions"
    r"(?:\?offset=(?P<offset>[^&]+))?$"
)
DEFAULT_PAGE_SIZE = 6
CURSOR_PREFIX = "cursor-"


class MarlstoneMockBank:
    """Serves the marlstone fixtures through an FDX-style cursor-paginated API."""

    def __init__(self, fixtures_dir: Path, page_size: int = DEFAULT_PAGE_SIZE) -> None:
        self._bank_dir = fixtures_dir / "marlstone"
        self._page_size = page_size

    def request(self, path: str) -> str:
        """Return the JSON body for ``path``."""
        if path == ACCOUNTS_PATH:
            return (self._bank_dir / "accounts.json").read_text(encoding="utf-8")
        match = TRANSACTIONS_PATH_PATTERN.fullmatch(path)
        if match is None:
            raise ValueError(f"unknown marlstone path: {path!r}")
        return self._transactions_page(match.group("account_id"), match.group("offset"))

    def _transactions_page(self, account_id: str, offset_cursor: str | None) -> str:
        if account_id not in self._known_account_ids():
            raise ValueError(f"unknown marlstone account: {account_id!r}")
        fixture = json.loads((self._bank_dir / "transactions.json").read_text(encoding="utf-8"))
        account_entries = [
            entry
            for entry in fixture["transactions"]
            if entry["depositTransaction"]["accountId"] == account_id
        ]
        page_start = self._parse_offset_cursor(offset_cursor, len(account_entries))
        page_end = page_start + self._page_size
        next_offset = f"{CURSOR_PREFIX}{page_end}" if page_end < len(account_entries) else None
        body = {
            "transactions": account_entries[page_start:page_end],
            "page": {"nextOffset": next_offset, "total": len(account_entries)},
        }
        return json.dumps(body)

    def _known_account_ids(self) -> set[str]:
        fixture = json.loads((self._bank_dir / "accounts.json").read_text(encoding="utf-8"))
        return {entry["depositAccount"]["accountId"] for entry in fixture["accounts"]}

    def _parse_offset_cursor(self, offset_cursor: str | None, entry_count: int) -> int:
        if offset_cursor is None:
            return 0
        cursor_value = offset_cursor.removeprefix(CURSOR_PREFIX)
        if cursor_value == offset_cursor or not cursor_value.isdigit():
            raise ValueError(f"invalid marlstone offset cursor: {offset_cursor!r}")
        page_start = int(cursor_value)
        if page_start >= entry_count:
            raise ValueError(f"marlstone offset cursor past the end: {offset_cursor!r}")
        return page_start
