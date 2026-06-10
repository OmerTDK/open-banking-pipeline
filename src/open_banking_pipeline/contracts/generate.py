"""Derive contract documents from the pydantic models and the landing column specs.

The code is the single source of truth: contracts are generated from
``CanonicalAccount``/``CanonicalTransaction`` (fields, types, nullability,
enum value sets, semantic notes from field descriptions) and from the landing
``LandingColumn`` specs that also build the DuckDB DDL. Hand-editing the
committed artifacts therefore cannot change the actual schema — the checker
compares them back against code on every run.
"""

from datetime import date
from decimal import Decimal
from enum import Enum
from types import NoneType, UnionType
from typing import Union, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from open_banking_pipeline.canonical import CanonicalAccount, CanonicalTransaction
from open_banking_pipeline.contracts.model import (
    CONTRACT_FORMAT_VERSION,
    Contract,
    FieldContract,
)
from open_banking_pipeline.contracts.versions import CONTRACT_VERSIONS
from open_banking_pipeline.ingestion.landing import (
    ACCOUNTS_LANDING_COLUMNS,
    TRANSACTIONS_LANDING_COLUMNS,
    LandingColumn,
)

SCALAR_TYPE_NAMES: dict[type, str] = {
    str: "string",
    Decimal: "decimal",
    date: "date",
    int: "integer",
    bool: "boolean",
}


def generate_all_contracts() -> dict[str, Contract]:
    """Generate the current contract for every subject, keyed by subject name."""
    return {
        "canonical_account": _contract_from_model("canonical_account", CanonicalAccount),
        "canonical_transaction": _contract_from_model(
            "canonical_transaction", CanonicalTransaction
        ),
        "landing_accounts": _contract_from_landing_columns(
            "landing_accounts", "accounts", ACCOUNTS_LANDING_COLUMNS
        ),
        "landing_transactions": _contract_from_landing_columns(
            "landing_transactions", "transactions", TRANSACTIONS_LANDING_COLUMNS
        ),
    }


def _contract_from_model(subject: str, model: type[BaseModel]) -> Contract:
    fields = tuple(
        _field_contract_from_pydantic(field_name, field_info)
        for field_name, field_info in model.model_fields.items()
    )
    return Contract(
        contract_format=CONTRACT_FORMAT_VERSION,
        subject=subject,
        version=CONTRACT_VERSIONS[subject],
        source=f"pydantic:{model.__module__}.{model.__qualname__}",
        fields=fields,
    )


def _field_contract_from_pydantic(field_name: str, field_info: FieldInfo) -> FieldContract:
    base_type, nullable = _unwrap_optional(field_name, field_info.annotation)
    enum_values = None
    if isinstance(base_type, type) and issubclass(base_type, Enum):
        enum_values = tuple(member.value for member in base_type)
        type_name = "string"
    elif base_type in SCALAR_TYPE_NAMES:
        type_name = SCALAR_TYPE_NAMES[base_type]
    else:
        raise ValueError(f"field {field_name!r}: no contract type mapping for {base_type!r}")
    return FieldContract(
        name=field_name,
        type=type_name,
        nullable=nullable,
        required=field_info.is_required(),
        enum_values=enum_values,
        doc=field_info.description,
    )


def _unwrap_optional(field_name: str, annotation: object) -> tuple[type, bool]:
    origin = get_origin(annotation)
    if origin is not UnionType and origin is not Union:
        if not isinstance(annotation, type):
            raise ValueError(f"field {field_name!r}: unsupported annotation {annotation!r}")
        return annotation, False
    non_none_members = [member for member in get_args(annotation) if member is not NoneType]
    if len(non_none_members) != 1 or not isinstance(non_none_members[0], type):
        raise ValueError(
            f"field {field_name!r}: only `T | None` unions are contractable, got {annotation!r}"
        )
    return non_none_members[0], True


def _contract_from_landing_columns(
    subject: str,
    table_name: str,
    columns: tuple[LandingColumn, ...],
) -> Contract:
    fields = tuple(
        FieldContract(
            name=column.name,
            type=column.sql_type.lower(),
            nullable=column.is_nullable,
            required=not column.is_nullable,
        )
        for column in columns
    )
    return Contract(
        contract_format=CONTRACT_FORMAT_VERSION,
        subject=subject,
        version=CONTRACT_VERSIONS[subject],
        source=f"duckdb-ddl:open_banking_pipeline.ingestion.landing.{table_name}",
        fields=fields,
    )
