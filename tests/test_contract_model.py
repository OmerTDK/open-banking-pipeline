"""Tests for the contract document model and its deterministic serialization."""

import pytest
from pydantic import ValidationError

from open_banking_pipeline.contracts.model import (
    Contract,
    FieldContract,
    parse_contract,
    serialize_contract,
)


def make_contract(version: str = "1.0.0") -> Contract:
    return Contract(
        contract_format=1,
        subject="canonical_transaction",
        version=version,
        source="pydantic:open_banking_pipeline.canonical.CanonicalTransaction",
        fields=(
            FieldContract(
                name="transaction_id",
                type="string",
                nullable=False,
                required=True,
                doc="Idempotency key.",
            ),
            FieldContract(
                name="status",
                type="string",
                nullable=False,
                required=True,
                enum_values=("booked", "pending"),
            ),
        ),
    )


class TestSerialization:
    def test_serialized_contract_is_sorted_keys_json_with_trailing_newline(self) -> None:
        serialized = serialize_contract(make_contract())

        assert serialized.endswith("}\n")
        key_positions = [serialized.index(f'"{key}"') for key in sorted(["subject", "version"])]
        assert key_positions == sorted(key_positions)

    def test_serialize_is_deterministic(self) -> None:
        assert serialize_contract(make_contract()) == serialize_contract(make_contract())

    def test_parse_round_trips_serialize(self) -> None:
        contract = make_contract()

        assert parse_contract(serialize_contract(contract)) == contract


class TestValidation:
    def test_unknown_keys_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="surprise"):
            parse_contract('{"surprise": true}')

    def test_non_semver_version_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="version"):
            make_contract(version="v1")

    def test_contract_without_fields_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="fields"):
            Contract(
                contract_format=1,
                subject="empty",
                version="1.0.0",
                source="nowhere",
                fields=(),
            )
