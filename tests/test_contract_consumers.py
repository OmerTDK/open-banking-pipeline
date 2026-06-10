"""Tests for consumer manifests: pinned fields, validation, and breaking-change vetoes."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from open_banking_pipeline.contracts.consumers import (
    ConsumedSubject,
    ConsumerManifest,
    consumer_veto_problems,
    load_consumer_manifests,
    manifest_problems,
    parse_consumer_manifest,
)
from open_banking_pipeline.contracts.diff import ChangeType, ContractChange
from open_banking_pipeline.contracts.model import Contract, FieldContract

SUBJECT = "canonical_transaction"


def make_manifest(
    fields: tuple[str, ...] = ("amount", "currency"),
    acknowledged_version: str = "1.0.0",
) -> ConsumerManifest:
    return ConsumerManifest(
        consumer="categorization_engine",
        consumes={
            SUBJECT: ConsumedSubject(fields=fields, acknowledged_version=acknowledged_version)
        },
    )


def make_contract(version: str = "1.0.0") -> Contract:
    return Contract(
        contract_format=1,
        subject=SUBJECT,
        version=version,
        source="pydantic:open_banking_pipeline.canonical.CanonicalTransaction",
        fields=(
            FieldContract(name="amount", type="decimal", nullable=False, required=True),
            FieldContract(name="currency", type="string", nullable=False, required=True),
        ),
    )


def breaking_change(field_name: str = "amount") -> ContractChange:
    return ContractChange(SUBJECT, ChangeType.TYPE_CHANGED, field_name, "type changed")


class TestManifestParsing:
    def test_manifest_round_trips_from_json(self) -> None:
        manifest = make_manifest()

        parsed = parse_consumer_manifest(manifest.model_dump_json())

        assert parsed == manifest

    def test_unknown_keys_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="surprise"):
            parse_consumer_manifest('{"consumer": "x", "consumes": {}, "surprise": 1}')

    def test_empty_pinned_fields_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="fields"):
            ConsumedSubject(fields=(), acknowledged_version="1.0.0")

    def test_manifests_load_from_a_directory_in_name_order(self, tmp_path: Path) -> None:
        (tmp_path / "b_engine.json").write_text(
            make_manifest().model_copy(update={"consumer": "b_engine"}).model_dump_json()
        )
        (tmp_path / "a_engine.json").write_text(
            make_manifest().model_copy(update={"consumer": "a_engine"}).model_dump_json()
        )

        manifests = load_consumer_manifests(tmp_path)

        assert [manifest.consumer for manifest in manifests] == ["a_engine", "b_engine"]

    def test_missing_directory_means_no_manifests(self, tmp_path: Path) -> None:
        assert load_consumer_manifests(tmp_path / "absent") == []


class TestManifestValidation:
    def test_clean_manifest_has_no_problems(self) -> None:
        assert manifest_problems(make_manifest(), {SUBJECT: make_contract()}) == []

    def test_pinning_a_missing_field_is_a_problem(self) -> None:
        manifest = make_manifest(fields=("amount", "vanished"))

        problems = manifest_problems(manifest, {SUBJECT: make_contract()})

        assert any("vanished" in problem for problem in problems)

    def test_consuming_an_unknown_subject_is_a_problem(self) -> None:
        problems = manifest_problems(make_manifest(), {})

        assert any(SUBJECT in problem for problem in problems)

    def test_acknowledging_a_future_version_is_a_problem(self) -> None:
        manifest = make_manifest(acknowledged_version="3.0.0")

        problems = manifest_problems(manifest, {SUBJECT: make_contract(version="1.0.0")})

        assert any("3.0.0" in problem for problem in problems)


class TestConsumerVetoes:
    def test_breaking_change_to_consumed_field_without_ack_is_vetoed(self) -> None:
        problems = consumer_veto_problems(
            [breaking_change("amount")],
            [make_manifest(acknowledged_version="1.0.0")],
            {SUBJECT: "2.0.0"},
        )

        assert len(problems) == 1
        assert "categorization_engine" in problems[0]
        assert "amount" in problems[0]

    def test_acknowledged_breaking_change_is_not_vetoed(self) -> None:
        problems = consumer_veto_problems(
            [breaking_change("amount")],
            [make_manifest(acknowledged_version="2.0.0")],
            {SUBJECT: "2.0.0"},
        )

        assert problems == []

    def test_breaking_change_to_unconsumed_field_is_not_vetoed(self) -> None:
        problems = consumer_veto_problems(
            [breaking_change("raw_category")],
            [make_manifest(fields=("amount", "currency"))],
            {SUBJECT: "2.0.0"},
        )

        assert problems == []

    def test_non_breaking_change_to_consumed_field_is_not_vetoed(self) -> None:
        added = ContractChange(SUBJECT, ChangeType.ENUM_VALUE_ADDED, "amount", "value added")

        assert consumer_veto_problems([added], [make_manifest()], {SUBJECT: "1.1.0"}) == []

    def test_breaking_change_to_unconsumed_subject_is_not_vetoed(self) -> None:
        change = ContractChange("landing_accounts", ChangeType.FIELD_REMOVED, "iban", "removed")

        problems = consumer_veto_problems(
            [change], [make_manifest()], {"landing_accounts": "2.0.0"}
        )

        assert problems == []
