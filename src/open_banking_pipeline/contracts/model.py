"""Contract document model: a versioned, serialized schema for one subject.

A contract is generated from code (pydantic models, landing column specs) and
committed under ``contracts/`` as canonical JSON — sorted keys, two-space
indent, trailing newline — so regenerating an unchanged contract is
byte-identical and any diff is a real change.
"""

import json

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_FORMAT_VERSION = 1
SEMVER_PATTERN = r"^\d+\.\d+\.\d+$"
JSON_INDENT = 2


class FieldContract(BaseModel):
    """One field of a contracted schema."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    type: str = Field(min_length=1)
    nullable: bool
    required: bool
    enum_values: tuple[str, ...] | None = None
    doc: str | None = None


class Contract(BaseModel):
    """A versioned schema contract for one subject (a model or a landing table)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_format: int
    subject: str = Field(min_length=1)
    version: str = Field(pattern=SEMVER_PATTERN)
    source: str = Field(min_length=1)
    fields: tuple[FieldContract, ...] = Field(min_length=1)


def serialize_contract(contract: Contract) -> str:
    """Serialize a contract to canonical JSON (deterministic byte-for-byte)."""
    return json.dumps(contract.model_dump(mode="json"), indent=JSON_INDENT, sort_keys=True) + "\n"


def parse_contract(text: str) -> Contract:
    """Parse and validate a serialized contract; unknown keys are rejected."""
    return Contract.model_validate_json(text)
