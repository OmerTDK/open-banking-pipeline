"""Tests for the deterministic failure schedules used by the mock bank APIs."""

import pytest

from open_banking_pipeline.mock_banks.failures import PlannedFailures


class TestPlannedFailures:
    def test_never_fails_for_any_request_index(self) -> None:
        plan = PlannedFailures.never()

        assert not any(plan.should_fail(index) for index in range(100))

    def test_explicit_indexes_fail_exactly_once_each(self) -> None:
        plan = PlannedFailures(failing_request_indexes=frozenset({1, 3}))

        outcomes = [plan.should_fail(index) for index in range(5)]

        assert outcomes == [False, True, False, True, False]

    def test_from_seed_is_deterministic(self) -> None:
        first = PlannedFailures.from_seed(seed=7, request_count=10, failure_count=3)
        second = PlannedFailures.from_seed(seed=7, request_count=10, failure_count=3)

        assert first.failing_request_indexes == second.failing_request_indexes

    def test_from_seed_plans_the_requested_failure_count(self) -> None:
        plan = PlannedFailures.from_seed(seed=7, request_count=10, failure_count=3)

        assert len(plan.failing_request_indexes) == 3
        assert all(0 <= index < 10 for index in plan.failing_request_indexes)

    def test_from_seed_rejects_failure_count_above_request_count(self) -> None:
        with pytest.raises(ValueError, match="failure_count"):
            PlannedFailures.from_seed(seed=7, request_count=2, failure_count=3)

    def test_from_seed_rejects_negative_failure_count(self) -> None:
        with pytest.raises(ValueError, match="failure_count"):
            PlannedFailures.from_seed(seed=7, request_count=2, failure_count=-1)
