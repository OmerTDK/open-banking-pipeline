"""Retry handling for the retryable bank API failures.

Rate limits honor the server's Retry-After hint; truncated downloads back
off exponentially. Anything that is not a ``BankApiError`` propagates
immediately — retrying an adapter bug only hides it.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass

from open_banking_pipeline.errors import RateLimitError, TruncatedExportError

DEFAULT_MAX_ATTEMPTS = 4
DEFAULT_BASE_DELAY_SECONDS = 0.5
BACKOFF_MULTIPLIER = 2


@dataclass(frozen=True)
class RetryPolicy:
    """How often to retry and how long to wait between attempts."""

    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    base_delay_seconds: float = DEFAULT_BASE_DELAY_SECONDS
    sleep: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1, got {self.max_attempts}")

    def backoff_delay_seconds(self, attempt: int) -> float:
        return self.base_delay_seconds * BACKOFF_MULTIPLIER ** (attempt - 1)


def fetch_with_retry[FetchedValue](
    fetch: Callable[[], FetchedValue],
    policy: RetryPolicy,
) -> FetchedValue:
    """Run ``fetch`` until it succeeds or the policy's attempts are exhausted.

    Args:
        fetch: Zero-argument callable performing one fetch attempt.
        policy: Attempt budget and sleep behavior.

    Returns:
        Whatever ``fetch`` returns on the first successful attempt.

    Raises:
        RateLimitError: The bank kept rate-limiting through the last attempt.
        TruncatedExportError: The download stayed truncated through the last attempt.
    """
    for attempt in range(1, policy.max_attempts + 1):
        is_last_attempt = attempt == policy.max_attempts
        try:
            return fetch()
        except RateLimitError as error:
            if is_last_attempt:
                raise
            policy.sleep(error.retry_after_seconds)
        except TruncatedExportError:
            if is_last_attempt:
                raise
            policy.sleep(policy.backoff_delay_seconds(attempt))
    raise AssertionError("unreachable: the loop either returns or raises")
