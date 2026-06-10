"""Deterministic failure schedules for the mock bank APIs.

Fault injection must be reproducible: the same seed always plans the same
failing request indexes, so a CI run that exercises 429s and truncated
downloads lands byte-identical data every time.
"""

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class PlannedFailures:
    """The set of request indexes (zero-based, per client) that must fail."""

    failing_request_indexes: frozenset[int]

    @classmethod
    def never(cls) -> "PlannedFailures":
        return cls(failing_request_indexes=frozenset())

    @classmethod
    def from_seed(cls, seed: int, request_count: int, failure_count: int) -> "PlannedFailures":
        """Plan ``failure_count`` failing indexes within ``request_count`` requests."""
        if failure_count < 0 or failure_count > request_count:
            raise ValueError(
                f"failure_count must be between 0 and request_count={request_count}, "
                f"got {failure_count}"
            )
        generator = random.Random(seed)
        failing_indexes = generator.sample(range(request_count), k=failure_count)
        return cls(failing_request_indexes=frozenset(failing_indexes))

    def should_fail(self, request_index: int) -> bool:
        return request_index in self.failing_request_indexes
