"""Canonical account and transaction schema shared by every bank adapter.

Idempotency key derivation (the replay-safety contract for all loads):

- ``account_id`` = ``{source_bank}:{source_account_id}``. Injective because
  ``SourceBank`` values never contain ``:``.
- ``transaction_id`` = SHA-256 hex digest of ``source_bank``,
  ``source_account_id`` and ``source_transaction_id`` joined by the ASCII
  unit separator (``\\x1f``). The join is injective because control
  characters (the separator included) are rejected in source identifiers,
  both in the derivation functions and at the model layer — without that
  enforcement, an identifier containing the separator could shift material
  between fields and collide with a different record.

Both identifiers are regular fields so canonical records survive a
serialize/re-validate round trip, but model validators recompute the
derivation and reject any mismatch — an adapter cannot ship a wrong key.
"""

import hashlib
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

KEY_SEPARATOR = "\x1f"
ISO_4217_PATTERN = r"^[A-Z]{3}$"


class SourceBank(StrEnum):
    """The three mock banks, each with a deliberately different API shape."""

    FJELLVIK = "fjellvik"
    MARLSTONE = "marlstone"
    TAKTWERK = "taktwerk"


class TransactionStatus(StrEnum):
    BOOKED = "booked"
    PENDING = "pending"


class TransactionCategory(StrEnum):
    """Normalized spend categories assigned by the (rule-based) categorizer."""

    GROCERIES = "groceries"
    DINING = "dining"
    TRANSPORT = "transport"
    UTILITIES = "utilities"
    RENT = "rent"
    SALARY = "salary"
    ENTERTAINMENT = "entertainment"
    HEALTHCARE = "healthcare"
    SHOPPING = "shopping"
    TRAVEL = "travel"
    CASH_WITHDRAWAL = "cash_withdrawal"
    TRANSFER = "transfer"
    BANK_FEES = "bank_fees"
    UNCATEGORIZED = "uncategorized"


def reject_control_characters(field_name: str, value: str) -> str:
    """Reject control characters that would break identifier-derivation injectivity."""
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(
            f"{field_name} {value!r} contains a control character; control characters "
            f"would break the injectivity of the derived identifiers"
        )
    return value


def derive_account_id(source_bank: SourceBank, source_account_id: str) -> str:
    """Derive the canonical account identifier for a source account."""
    reject_control_characters("source_account_id", source_account_id)
    return f"{source_bank.value}:{source_account_id}"


def derive_transaction_id(
    source_bank: SourceBank,
    source_account_id: str,
    source_transaction_id: str,
) -> str:
    """Derive the canonical transaction identifier (idempotency key).

    The same source transaction always derives the same key, so replayed
    loads are no-ops; distinct banks or accounts never collide.
    """
    reject_control_characters("source_account_id", source_account_id)
    reject_control_characters("source_transaction_id", source_transaction_id)
    key_material = KEY_SEPARATOR.join([source_bank.value, source_account_id, source_transaction_id])
    return hashlib.sha256(key_material.encode("utf-8")).hexdigest()


class CanonicalAccount(BaseModel):
    """A bank account normalized into the canonical schema."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    account_id: str
    source_bank: SourceBank
    source_account_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    currency: str = Field(pattern=ISO_4217_PATTERN)
    iban: str | None = None

    @field_validator("source_account_id")
    @classmethod
    def reject_control_characters_in_source_account_id(cls, value: str) -> str:
        return reject_control_characters("source_account_id", value)

    @model_validator(mode="after")
    def verify_account_id_derivation(self) -> Self:
        expected = derive_account_id(self.source_bank, self.source_account_id)
        if self.account_id != expected:
            raise ValueError(
                f"account_id {self.account_id!r} does not match the documented "
                f"derivation {expected!r}"
            )
        return self


class CanonicalTransaction(BaseModel):
    """A transaction normalized into the canonical schema.

    ``amount`` is signed in the account currency: negative for outflows,
    positive for inflows (refunds and salary are positive). Zero amounts are
    rejected because in practice they indicate a parsing bug upstream.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    transaction_id: str
    account_id: str
    source_bank: SourceBank
    source_account_id: str = Field(min_length=1)
    source_transaction_id: str = Field(min_length=1)
    status: TransactionStatus
    booking_date: date | None = None
    value_date: date | None = None
    amount: Decimal
    currency: str = Field(pattern=ISO_4217_PATTERN)
    counterparty_name: str | None = None
    counterparty_account: str | None = None
    description: str | None = None
    raw_category: str | None = None
    category: TransactionCategory = TransactionCategory.UNCATEGORIZED

    @field_validator("source_account_id")
    @classmethod
    def reject_control_characters_in_source_account_id(cls, value: str) -> str:
        return reject_control_characters("source_account_id", value)

    @field_validator("source_transaction_id")
    @classmethod
    def reject_control_characters_in_source_transaction_id(cls, value: str) -> str:
        return reject_control_characters("source_transaction_id", value)

    @field_validator("amount")
    @classmethod
    def reject_zero_amount(cls, amount: Decimal) -> Decimal:
        if amount == 0:
            raise ValueError("amount must not be zero; zero indicates an upstream parsing bug")
        return amount

    @model_validator(mode="after")
    def verify_identifier_derivations(self) -> Self:
        expected_account_id = derive_account_id(self.source_bank, self.source_account_id)
        if self.account_id != expected_account_id:
            raise ValueError(
                f"account_id {self.account_id!r} does not match the documented "
                f"derivation {expected_account_id!r}"
            )
        expected_transaction_id = derive_transaction_id(
            self.source_bank, self.source_account_id, self.source_transaction_id
        )
        if self.transaction_id != expected_transaction_id:
            raise ValueError(
                f"transaction_id {self.transaction_id!r} does not match the documented "
                f"derivation {expected_transaction_id!r}"
            )
        return self

    @model_validator(mode="after")
    def verify_booked_transaction_has_booking_date(self) -> Self:
        if self.status is TransactionStatus.BOOKED and self.booking_date is None:
            raise ValueError("booking_date is required when status is booked")
        return self
