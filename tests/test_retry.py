"""Tests for retry handling of rate limits and truncated downloads."""

import pytest

from open_banking_pipeline.errors import (
    BankApiError,
    RateLimitError,
    TruncatedExportError,
)
from open_banking_pipeline.ingestion.retry import RetryPolicy, fetch_with_retry


class RecordingSleeper:
    def __init__(self) -> None:
        self.sleeps: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.sleeps.append(seconds)


class FlakyFetch:
    """Fetch stub that raises the queued errors, then returns a value."""

    def __init__(self, errors: list[Exception], value: str = "payload") -> None:
        self._errors = list(errors)
        self._value = value
        self.call_count = 0

    def __call__(self) -> str:
        self.call_count += 1
        if self._errors:
            raise self._errors.pop(0)
        return self._value


def make_policy(sleeper: RecordingSleeper, max_attempts: int = 4) -> RetryPolicy:
    return RetryPolicy(max_attempts=max_attempts, base_delay_seconds=0.5, sleep=sleeper)


class TestErrorTypes:
    def test_rate_limit_error_is_a_bank_api_error(self) -> None:
        assert issubclass(RateLimitError, BankApiError)

    def test_truncated_export_error_is_a_bank_api_error(self) -> None:
        assert issubclass(TruncatedExportError, BankApiError)

    def test_rate_limit_error_carries_retry_after(self) -> None:
        error = RateLimitError(retry_after_seconds=1.5)

        assert error.retry_after_seconds == 1.5


class TestFetchWithRetry:
    def test_returns_value_when_fetch_succeeds_first_try(self) -> None:
        sleeper = RecordingSleeper()

        value = fetch_with_retry(FlakyFetch([]), make_policy(sleeper))

        assert value == "payload"
        assert sleeper.sleeps == []

    def test_rate_limit_sleeps_for_retry_after_then_succeeds(self) -> None:
        sleeper = RecordingSleeper()
        fetch = FlakyFetch([RateLimitError(retry_after_seconds=2.0)])

        value = fetch_with_retry(fetch, make_policy(sleeper))

        assert value == "payload"
        assert fetch.call_count == 2
        assert sleeper.sleeps == [2.0]

    def test_truncated_export_backs_off_exponentially(self) -> None:
        sleeper = RecordingSleeper()
        fetch = FlakyFetch([TruncatedExportError("cut short"), TruncatedExportError("cut short")])

        value = fetch_with_retry(fetch, make_policy(sleeper))

        assert value == "payload"
        assert sleeper.sleeps == [0.5, 1.0]

    def test_persistent_rate_limit_raises_after_max_attempts(self) -> None:
        sleeper = RecordingSleeper()
        fetch = FlakyFetch([RateLimitError(retry_after_seconds=1.0)] * 10)

        with pytest.raises(RateLimitError):
            fetch_with_retry(fetch, make_policy(sleeper, max_attempts=3))

        assert fetch.call_count == 3
        assert sleeper.sleeps == [1.0, 1.0]

    def test_persistent_truncation_raises_after_max_attempts(self) -> None:
        sleeper = RecordingSleeper()
        fetch = FlakyFetch([TruncatedExportError("cut short")] * 10)

        with pytest.raises(TruncatedExportError):
            fetch_with_retry(fetch, make_policy(sleeper, max_attempts=3))

        assert fetch.call_count == 3

    def test_non_retryable_error_propagates_immediately(self) -> None:
        sleeper = RecordingSleeper()
        fetch = FlakyFetch([ValueError("adapter bug")])

        with pytest.raises(ValueError, match="adapter bug"):
            fetch_with_retry(fetch, make_policy(sleeper))

        assert fetch.call_count == 1
        assert sleeper.sleeps == []

    def test_rejects_max_attempts_below_one(self) -> None:
        with pytest.raises(ValueError, match="max_attempts"):
            RetryPolicy(max_attempts=0, base_delay_seconds=0.5, sleep=RecordingSleeper())
