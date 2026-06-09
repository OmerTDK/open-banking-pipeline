"""Replace with real tests; keeps CI honest from the first commit."""

import sys


def test_python_version() -> None:
    assert sys.version_info >= (3, 12)
