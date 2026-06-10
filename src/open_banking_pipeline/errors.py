"""Operational error types raised when talking to the (mock) bank APIs.

These model transport-level failures a real connector must survive:
``RateLimitError`` is an HTTP 429 with a ``Retry-After`` header, and
``TruncatedExportError`` is a file download that was cut off mid-transfer.
Both are retryable; anything else (adapter bugs, schema drift) is not and
must propagate loudly.
"""


class BankApiError(Exception):
    """Operational failure while fetching from a bank API; retryable."""


class RateLimitError(BankApiError):
    """The bank rejected the request with HTTP 429 and a Retry-After hint."""

    def __init__(self, retry_after_seconds: float) -> None:
        super().__init__(f"rate limited; retry after {retry_after_seconds}s")
        self.retry_after_seconds = retry_after_seconds


class TruncatedExportError(BankApiError):
    """A whole-file export arrived incomplete and must be re-downloaded."""
