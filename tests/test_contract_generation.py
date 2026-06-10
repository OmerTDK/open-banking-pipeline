"""Tests for deriving contracts from the pydantic models and the landing column specs."""

import pytest

from open_banking_pipeline.canonical import (
    CanonicalAccount,
    CanonicalTransaction,
    SourceBank,
    TransactionCategory,
    TransactionStatus,
)
from open_banking_pipeline.contracts.generate import generate_all_contracts
from open_banking_pipeline.contracts.model import Contract, serialize_contract
from open_banking_pipeline.contracts.versions import CONTRACT_VERSIONS
from open_banking_pipeline.ingestion.landing import (
    ACCOUNTS_LANDING_COLUMNS,
    TRANSACTIONS_LANDING_COLUMNS,
)

EXPECTED_SUBJECTS = {
    "canonical_account",
    "canonical_transaction",
    "landing_accounts",
    "landing_transactions",
}


@pytest.fixture(scope="module")
def contracts() -> dict[str, Contract]:
    return generate_all_contracts()


class TestSubjectsAndVersions:
    def test_all_four_subjects_are_generated(self, contracts: dict[str, Contract]) -> None:
        assert set(contracts) == EXPECTED_SUBJECTS

    def test_every_subject_has_a_declared_version(self, contracts: dict[str, Contract]) -> None:
        assert set(CONTRACT_VERSIONS) == EXPECTED_SUBJECTS
        for subject, contract in contracts.items():
            assert contract.version == CONTRACT_VERSIONS[subject]

    def test_every_contract_names_its_code_source(self, contracts: dict[str, Contract]) -> None:
        assert contracts["canonical_transaction"].source == (
            "pydantic:open_banking_pipeline.canonical.CanonicalTransaction"
        )
        assert contracts["landing_transactions"].source == (
            "duckdb-ddl:open_banking_pipeline.ingestion.landing.transactions"
        )


class TestCanonicalModelContracts:
    def test_field_order_follows_model_declaration_order(
        self, contracts: dict[str, Contract]
    ) -> None:
        field_names = [field.name for field in contracts["canonical_transaction"].fields]
        assert field_names == list(CanonicalTransaction.model_fields)

        account_field_names = [field.name for field in contracts["canonical_account"].fields]
        assert account_field_names == list(CanonicalAccount.model_fields)

    def test_scalar_types_are_normalized(self, contracts: dict[str, Contract]) -> None:
        fields = {field.name: field for field in contracts["canonical_transaction"].fields}

        assert fields["transaction_id"].type == "string"
        assert fields["amount"].type == "decimal"
        assert fields["booking_date"].type == "date"
        assert fields["currency"].type == "string"

    def test_nullability_mirrors_optional_annotations(self, contracts: dict[str, Contract]) -> None:
        fields = {field.name: field for field in contracts["canonical_transaction"].fields}

        assert not fields["transaction_id"].nullable
        assert fields["booking_date"].nullable
        assert fields["counterparty_name"].nullable
        assert not fields["amount"].nullable

    def test_required_mirrors_pydantic_defaults(self, contracts: dict[str, Contract]) -> None:
        fields = {field.name: field for field in contracts["canonical_transaction"].fields}

        assert fields["amount"].required
        assert not fields["category"].required
        assert not fields["booking_date"].required

        account_fields = {field.name: field for field in contracts["canonical_account"].fields}
        assert not account_fields["iban"].required

    def test_enum_fields_carry_their_full_value_sets(self, contracts: dict[str, Contract]) -> None:
        fields = {field.name: field for field in contracts["canonical_transaction"].fields}

        assert fields["status"].enum_values == tuple(member.value for member in TransactionStatus)
        assert fields["category"].enum_values == tuple(
            member.value for member in TransactionCategory
        )
        assert fields["source_bank"].enum_values == tuple(member.value for member in SourceBank)
        assert fields["amount"].enum_values is None

    def test_every_canonical_field_has_a_semantic_note(
        self, contracts: dict[str, Contract]
    ) -> None:
        for subject in ("canonical_account", "canonical_transaction"):
            for field in contracts[subject].fields:
                assert field.doc, f"{subject}.{field.name} has no semantic note"


class TestLandingTableContracts:
    def test_landing_fields_mirror_the_column_specs(self, contracts: dict[str, Contract]) -> None:
        for subject, columns in (
            ("landing_accounts", ACCOUNTS_LANDING_COLUMNS),
            ("landing_transactions", TRANSACTIONS_LANDING_COLUMNS),
        ):
            fields = contracts[subject].fields
            assert [field.name for field in fields] == [column.name for column in columns]
            for field, column in zip(fields, columns, strict=True):
                assert field.type == column.sql_type.lower()
                assert field.nullable == column.is_nullable
                assert field.required == (not column.is_nullable)
                assert field.primary_key == column.is_primary_key
                assert field.enum_values is None

    def test_landing_primary_keys_are_part_of_the_contract(
        self, contracts: dict[str, Contract]
    ) -> None:
        transaction_fields = {
            field.name: field for field in contracts["landing_transactions"].fields
        }
        account_fields = {field.name: field for field in contracts["landing_accounts"].fields}

        assert transaction_fields["transaction_id"].primary_key
        assert account_fields["account_id"].primary_key
        assert not transaction_fields["amount"].primary_key
        assert not transaction_fields["account_id"].primary_key

    def test_canonical_model_fields_carry_no_primary_key(
        self, contracts: dict[str, Contract]
    ) -> None:
        for subject in ("canonical_account", "canonical_transaction"):
            for field in contracts[subject].fields:
                assert not field.primary_key, f"{subject}.{field.name}"

    def test_landing_amount_pins_precision_and_scale(self, contracts: dict[str, Contract]) -> None:
        fields = {field.name: field for field in contracts["landing_transactions"].fields}

        assert fields["amount"].type == "decimal(18, 4)"


class TestRoundTripStability:
    def test_generating_twice_serializes_byte_identically(self) -> None:
        first = {
            subject: serialize_contract(contract)
            for subject, contract in generate_all_contracts().items()
        }
        second = {
            subject: serialize_contract(contract)
            for subject, contract in generate_all_contracts().items()
        }

        assert first == second
