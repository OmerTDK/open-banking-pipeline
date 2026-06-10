"""Legacy-CSV mock bank: whole-file downloads with planned silent truncation.

Interaction shape (ADR-0003): no pagination and no request/path surface —
just file downloads. A planned failure models a dropped connection: the
download returns truncated content without raising, exactly like a naive
HTTP/SFTP fetch that never checks Content-Length. Detecting the truncation
is deliberately the consumer's job (the adapter validates the file).
"""

from pathlib import Path

from open_banking_pipeline.mock_banks.failures import PlannedFailures

TRUNCATION_FRACTION = 0.6


class TaktwerkMockBank:
    """Serves the taktwerk fixtures as whole-file CSV downloads."""

    def __init__(
        self,
        fixtures_dir: Path,
        planned_failures: PlannedFailures | None = None,
    ) -> None:
        self._bank_dir = fixtures_dir / "taktwerk"
        self._planned_failures = planned_failures or PlannedFailures.never()
        self._download_index = 0

    def download_accounts_csv(self) -> str:
        return (self._bank_dir / "accounts.csv").read_text(encoding="utf-8")

    def download_transactions_export(self) -> str:
        """Return the transactions export, silently truncated on planned failures."""
        full_text = (self._bank_dir / "transactions_export.csv").read_text(encoding="utf-8")
        download_index = self._download_index
        self._download_index += 1
        if self._planned_failures.should_fail(download_index):
            return full_text[: int(len(full_text) * TRUNCATION_FRACTION)]
        return full_text
