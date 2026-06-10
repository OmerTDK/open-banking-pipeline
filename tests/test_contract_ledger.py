"""Tests for the append-only subjects ledger that anchors the contract baseline."""

import json
from pathlib import Path

import pytest

from open_banking_pipeline.contracts.ledger import (
    LEDGER_FILENAME,
    ledger_problems,
    load_ledger,
    merged_ledger,
    serialize_ledger,
)


def write_ledger(directory: Path, ledger: dict) -> Path:
    ledger_path = directory / LEDGER_FILENAME
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    return ledger_path


class TestLoadLedger:
    def test_missing_ledger_loads_as_none(self, tmp_path: Path) -> None:
        assert load_ledger(tmp_path) is None

    def test_serialized_ledger_round_trips(self, tmp_path: Path) -> None:
        ledger = {"canonical_transaction": "1.2.0", "landing_accounts": "1.0.0"}
        (tmp_path / LEDGER_FILENAME).write_text(serialize_ledger(ledger), encoding="utf-8")

        assert load_ledger(tmp_path) == ledger

    def test_serialization_is_canonical(self) -> None:
        ledger = {"b_subject": "1.0.0", "a_subject": "2.0.0"}

        serialized = serialize_ledger(ledger)

        assert serialized == serialize_ledger(dict(reversed(ledger.items())))
        assert serialized.endswith("\n")

    def test_malformed_version_is_rejected(self, tmp_path: Path) -> None:
        write_ledger(tmp_path, {"canonical_transaction": "not-a-version"})

        with pytest.raises(ValueError, match="semver"):
            load_ledger(tmp_path)

    def test_non_object_ledger_is_rejected(self, tmp_path: Path) -> None:
        (tmp_path / LEDGER_FILENAME).write_text("[]", encoding="utf-8")

        with pytest.raises(ValueError, match="subject"):
            load_ledger(tmp_path)


class TestLedgerProblems:
    def test_consistent_state_has_no_problems(self) -> None:
        versions = {"canonical_transaction": "1.0.0"}

        assert ledger_problems(versions, versions) == []

    def test_no_ledger_and_no_artifacts_is_clean(self) -> None:
        assert ledger_problems(None, {}) == []

    def test_artifacts_without_a_ledger_are_a_problem(self) -> None:
        problems = ledger_problems(None, {"canonical_transaction": "1.0.0"})

        assert any("ledger is missing" in problem for problem in problems)

    def test_recorded_subject_with_missing_artifact_is_a_problem(self) -> None:
        problems = ledger_problems({"canonical_transaction": "1.0.0"}, {})

        assert any("never vanish" in problem for problem in problems)

    def test_unrecorded_committed_artifact_is_a_problem(self) -> None:
        problems = ledger_problems({}, {"canonical_transaction": "1.0.0"})

        assert any("not recorded" in problem for problem in problems)

    def test_artifact_version_behind_the_ledger_is_a_problem(self) -> None:
        problems = ledger_problems(
            {"canonical_transaction": "2.0.0"}, {"canonical_transaction": "1.0.0"}
        )

        assert any("behind" in problem for problem in problems)

    def test_artifact_version_ahead_of_the_ledger_is_not_a_ledger_problem(self) -> None:
        problems = ledger_problems(
            {"canonical_transaction": "1.0.0"}, {"canonical_transaction": "2.0.0"}
        )

        assert problems == []


class TestMergedLedger:
    def test_new_subjects_are_added(self) -> None:
        merged = merged_ledger({"existing": "1.0.0"}, {"added": "1.0.0"})

        assert merged == {"existing": "1.0.0", "added": "1.0.0"}

    def test_versions_move_forward(self) -> None:
        merged = merged_ledger({"subject": "1.0.0"}, {"subject": "2.0.0"})

        assert merged == {"subject": "2.0.0"}

    def test_versions_never_move_backwards(self) -> None:
        merged = merged_ledger({"subject": "2.0.0"}, {"subject": "1.0.0"})

        assert merged == {"subject": "2.0.0"}

    def test_absent_ledger_merges_as_empty(self) -> None:
        assert merged_ledger(None, {"subject": "1.0.0"}) == {"subject": "1.0.0"}
