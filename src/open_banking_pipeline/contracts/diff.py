"""Classify the changes between two versions of a contract.

Compatibility stance (ADR-0004): every change is classified from the union of
the producer and consumer perspectives, so a change that breaks either side is
breaking. That is why nullability changes are breaking in *both* directions —
widening breaks readers (new nulls), narrowing breaks writers (null no longer
accepted) — and why dropping an enum constraint is breaking even though adding
one value is not.
"""

import re
from dataclasses import dataclass
from enum import IntEnum, StrEnum

from open_banking_pipeline.contracts.model import Contract, FieldContract

SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


class ChangeCategory(StrEnum):
    BREAKING = "breaking"
    NON_BREAKING = "non_breaking"
    DOCUMENTATION = "documentation"


class ChangeType(StrEnum):
    FIELD_REMOVED = "field_removed"
    TYPE_CHANGED = "type_changed"
    NULLABILITY_CHANGED = "nullability_changed"
    FIELD_BECAME_REQUIRED = "field_became_required"
    ENUM_VALUE_REMOVED = "enum_value_removed"
    ENUM_CONSTRAINT_ADDED = "enum_constraint_added"
    ENUM_CONSTRAINT_REMOVED = "enum_constraint_removed"
    REQUIRED_FIELD_ADDED = "required_field_added"
    OPTIONAL_FIELD_ADDED = "optional_field_added"
    ENUM_VALUE_ADDED = "enum_value_added"
    FIELD_BECAME_OPTIONAL = "field_became_optional"
    DOC_CHANGED = "doc_changed"


CHANGE_CATEGORIES: dict[ChangeType, ChangeCategory] = {
    ChangeType.FIELD_REMOVED: ChangeCategory.BREAKING,
    ChangeType.TYPE_CHANGED: ChangeCategory.BREAKING,
    ChangeType.NULLABILITY_CHANGED: ChangeCategory.BREAKING,
    ChangeType.FIELD_BECAME_REQUIRED: ChangeCategory.BREAKING,
    ChangeType.ENUM_VALUE_REMOVED: ChangeCategory.BREAKING,
    ChangeType.ENUM_CONSTRAINT_ADDED: ChangeCategory.BREAKING,
    ChangeType.ENUM_CONSTRAINT_REMOVED: ChangeCategory.BREAKING,
    ChangeType.REQUIRED_FIELD_ADDED: ChangeCategory.BREAKING,
    ChangeType.OPTIONAL_FIELD_ADDED: ChangeCategory.NON_BREAKING,
    ChangeType.ENUM_VALUE_ADDED: ChangeCategory.NON_BREAKING,
    ChangeType.FIELD_BECAME_OPTIONAL: ChangeCategory.NON_BREAKING,
    ChangeType.DOC_CHANGED: ChangeCategory.DOCUMENTATION,
}


class BumpLevel(IntEnum):
    NONE = 0
    PATCH = 1
    MINOR = 2
    MAJOR = 3


CATEGORY_BUMP_LEVELS: dict[ChangeCategory, BumpLevel] = {
    ChangeCategory.BREAKING: BumpLevel.MAJOR,
    ChangeCategory.NON_BREAKING: BumpLevel.MINOR,
    ChangeCategory.DOCUMENTATION: BumpLevel.PATCH,
}


@dataclass(frozen=True)
class ContractChange:
    """One classified difference between two versions of a contract."""

    subject: str
    change_type: ChangeType
    field_name: str
    detail: str

    @property
    def category(self) -> ChangeCategory:
        return CHANGE_CATEGORIES[self.change_type]


def diff_contracts(old: Contract, new: Contract) -> list[ContractChange]:
    """Return every classified field-level change from ``old`` to ``new``."""
    if old.subject != new.subject:
        raise ValueError(f"cannot diff different subjects: {old.subject!r} vs {new.subject!r}")
    old_fields = {field.name: field for field in old.fields}
    new_fields = {field.name: field for field in new.fields}

    changes = []
    for name, old_field in old_fields.items():
        if name not in new_fields:
            changes.append(
                ContractChange(old.subject, ChangeType.FIELD_REMOVED, name, "field removed")
            )
            continue
        changes.extend(_diff_field(old.subject, old_field, new_fields[name]))
    for name, new_field in new_fields.items():
        if name not in old_fields:
            changes.append(_field_added_change(old.subject, new_field))
    return changes


def _field_added_change(subject: str, field: FieldContract) -> ContractChange:
    if field.required:
        return ContractChange(
            subject,
            ChangeType.REQUIRED_FIELD_ADDED,
            field.name,
            "new field must be provided by every producer",
        )
    return ContractChange(
        subject, ChangeType.OPTIONAL_FIELD_ADDED, field.name, "new optional field"
    )


def _diff_field(subject: str, old: FieldContract, new: FieldContract) -> list[ContractChange]:
    changes = []
    if old.type != new.type:
        changes.append(
            ContractChange(
                subject,
                ChangeType.TYPE_CHANGED,
                old.name,
                f"type changed from {old.type!r} to {new.type!r}",
            )
        )
    if old.nullable != new.nullable:
        direction = "nullable" if new.nullable else "non-nullable"
        changes.append(
            ContractChange(
                subject,
                ChangeType.NULLABILITY_CHANGED,
                old.name,
                f"field became {direction}",
            )
        )
    if old.required != new.required:
        change_type = (
            ChangeType.FIELD_BECAME_REQUIRED if new.required else ChangeType.FIELD_BECAME_OPTIONAL
        )
        changes.append(
            ContractChange(
                subject, change_type, old.name, f"required: {old.required} -> {new.required}"
            )
        )
    changes.extend(_diff_enum_values(subject, old, new))
    if old.doc != new.doc:
        changes.append(
            ContractChange(subject, ChangeType.DOC_CHANGED, old.name, "semantic note changed")
        )
    return changes


def _diff_enum_values(subject: str, old: FieldContract, new: FieldContract) -> list[ContractChange]:
    if old.enum_values is None and new.enum_values is None:
        return []
    if old.enum_values is None:
        return [
            ContractChange(
                subject,
                ChangeType.ENUM_CONSTRAINT_ADDED,
                old.name,
                "free value domain became an enum",
            )
        ]
    if new.enum_values is None:
        return [
            ContractChange(
                subject,
                ChangeType.ENUM_CONSTRAINT_REMOVED,
                old.name,
                "enum became a free value domain",
            )
        ]
    changes = []
    for value in old.enum_values:
        if value not in new.enum_values:
            changes.append(
                ContractChange(
                    subject,
                    ChangeType.ENUM_VALUE_REMOVED,
                    old.name,
                    f"enum value {value!r} removed",
                )
            )
    for value in new.enum_values:
        if value not in old.enum_values:
            changes.append(
                ContractChange(
                    subject,
                    ChangeType.ENUM_VALUE_ADDED,
                    old.name,
                    f"enum value {value!r} added",
                )
            )
    return changes


def required_bump(changes: list[ContractChange]) -> BumpLevel:
    """Return the minimum version bump the given changes demand."""
    if not changes:
        return BumpLevel.NONE
    return max(CATEGORY_BUMP_LEVELS[change.category] for change in changes)


def parse_version(version: str) -> tuple[int, int, int]:
    """Parse a strict ``major.minor.patch`` version into an ordered tuple."""
    match = SEMVER_RE.match(version)
    if match is None:
        raise ValueError(f"{version!r} is not a semver-style major.minor.patch version")
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def version_change_problems(
    old_version: str,
    new_version: str,
    required: BumpLevel,
) -> list[str]:
    """Return why the version change does not satisfy the required bump (empty = OK)."""
    old_parts = parse_version(old_version)
    new_parts = parse_version(new_version)
    if new_parts < old_parts:
        return [f"version went backwards: {old_version} -> {new_version}"]
    if required is BumpLevel.NONE:
        if new_parts != old_parts:
            return [f"version changed ({old_version} -> {new_version}) without contract changes"]
        return []
    if required is BumpLevel.MAJOR:
        if new_parts[0] <= old_parts[0]:
            return [
                f"breaking changes require a major bump: {old_version} -> {new_version} "
                f"is not a major increase"
            ]
        return []
    if required is BumpLevel.MINOR:
        has_minor_bump = new_parts[0] > old_parts[0] or new_parts[1] > old_parts[1]
        if not has_minor_bump:
            return [
                f"non-breaking schema changes require at least a minor bump: "
                f"{old_version} -> {new_version} is not one"
            ]
        return []
    if new_parts == old_parts:
        return [
            f"documentation changes require at least a patch bump: version stayed {old_version}"
        ]
    return []
