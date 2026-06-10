"""Tests for the breaking-change classifier and the semver-ish bump rules."""

import pytest

from open_banking_pipeline.contracts.diff import (
    BumpLevel,
    ChangeCategory,
    ChangeType,
    ContractChange,
    diff_contracts,
    parse_version,
    required_bump,
    version_change_problems,
)
from open_banking_pipeline.contracts.model import Contract, FieldContract


def make_field(
    name: str = "amount",
    type: str = "decimal",
    nullable: bool = False,
    required: bool = True,
    primary_key: bool = False,
    enum_values: tuple[str, ...] | None = None,
    doc: str | None = "Signed amount",
) -> FieldContract:
    return FieldContract(
        name=name,
        type=type,
        nullable=nullable,
        required=required,
        primary_key=primary_key,
        enum_values=enum_values,
        doc=doc,
    )


def make_contract(*fields: FieldContract, version: str = "1.0.0") -> Contract:
    return Contract(
        contract_format=1,
        subject="canonical_transaction",
        version=version,
        source="pydantic:open_banking_pipeline.canonical.CanonicalTransaction",
        fields=fields or (make_field(),),
    )


def single_change(old: Contract, new: Contract) -> ContractChange:
    changes = diff_contracts(old, new)
    assert len(changes) == 1, changes
    return changes[0]


class TestBreakingChanges:
    @pytest.mark.parametrize(
        ("nullable", "required"),
        [(False, True), (False, False), (True, False)],
        ids=["required", "optional", "nullable-optional"],
    )
    def test_removed_field_is_breaking(self, nullable: bool, required: bool) -> None:
        removed = make_field("note", type="string", nullable=nullable, required=required)
        old = make_contract(make_field("amount"), removed)
        new = make_contract(make_field("amount"))

        change = single_change(old, new)

        assert change.change_type is ChangeType.FIELD_REMOVED
        assert change.category is ChangeCategory.BREAKING
        assert change.field_name == "note"

    def test_changed_type_is_breaking(self) -> None:
        old = make_contract(make_field("amount", type="decimal"))
        new = make_contract(make_field("amount", type="string"))

        change = single_change(old, new)

        assert change.change_type is ChangeType.TYPE_CHANGED
        assert change.category is ChangeCategory.BREAKING
        assert "decimal" in change.detail
        assert "string" in change.detail

    def test_removed_enum_value_is_breaking(self) -> None:
        old = make_contract(make_field("status", type="string", enum_values=("booked", "pending")))
        new = make_contract(make_field("status", type="string", enum_values=("booked",)))

        change = single_change(old, new)

        assert change.change_type is ChangeType.ENUM_VALUE_REMOVED
        assert change.category is ChangeCategory.BREAKING
        assert "pending" in change.detail

    def test_new_required_field_is_breaking(self) -> None:
        old = make_contract(make_field("amount"))
        new = make_contract(make_field("amount"), make_field("currency", type="string"))

        change = single_change(old, new)

        assert change.change_type is ChangeType.REQUIRED_FIELD_ADDED
        assert change.category is ChangeCategory.BREAKING
        assert change.field_name == "currency"

    @pytest.mark.parametrize(
        ("old_nullable", "new_nullable", "expected_direction"),
        [(False, True, "nullable"), (True, False, "non-nullable")],
        ids=["widened", "narrowed"],
    )
    def test_nullability_change_is_breaking_in_both_directions(
        self, old_nullable: bool, new_nullable: bool, expected_direction: str
    ) -> None:
        old = make_contract(make_field("amount", nullable=old_nullable, required=False))
        new = make_contract(make_field("amount", nullable=new_nullable, required=False))

        change = single_change(old, new)

        assert change.change_type is ChangeType.NULLABILITY_CHANGED
        assert change.category is ChangeCategory.BREAKING
        assert change.detail == f"field became {expected_direction}"

    def test_field_becoming_required_is_breaking(self) -> None:
        old = make_contract(make_field("amount", required=False))
        new = make_contract(make_field("amount", required=True))

        change = single_change(old, new)

        assert change.change_type is ChangeType.FIELD_BECAME_REQUIRED
        assert change.category is ChangeCategory.BREAKING

    def test_primary_key_added_is_breaking(self) -> None:
        old = make_contract(make_field("transaction_id", type="varchar"))
        new = make_contract(make_field("transaction_id", type="varchar", primary_key=True))

        change = single_change(old, new)

        assert change.change_type is ChangeType.PRIMARY_KEY_ADDED
        assert change.category is ChangeCategory.BREAKING
        assert change.field_name == "transaction_id"

    def test_primary_key_removed_is_breaking(self) -> None:
        old = make_contract(make_field("transaction_id", type="varchar", primary_key=True))
        new = make_contract(make_field("transaction_id", type="varchar"))

        change = single_change(old, new)

        assert change.change_type is ChangeType.PRIMARY_KEY_REMOVED
        assert change.category is ChangeCategory.BREAKING
        assert change.field_name == "transaction_id"

    def test_retyped_primary_key_column_is_breaking(self) -> None:
        old = make_contract(make_field("transaction_id", type="varchar", primary_key=True))
        new = make_contract(make_field("transaction_id", type="integer", primary_key=True))

        change = single_change(old, new)

        assert change.change_type is ChangeType.TYPE_CHANGED
        assert change.category is ChangeCategory.BREAKING

    @pytest.mark.parametrize(
        ("old_values", "new_values", "expected_change_type"),
        [
            (None, ("booked", "pending"), ChangeType.ENUM_CONSTRAINT_ADDED),
            (("booked", "pending"), None, ChangeType.ENUM_CONSTRAINT_REMOVED),
        ],
        ids=["constraint-added", "constraint-removed"],
    )
    def test_enum_constraint_appearing_or_disappearing_is_breaking(
        self,
        old_values: tuple[str, ...] | None,
        new_values: tuple[str, ...] | None,
        expected_change_type: ChangeType,
    ) -> None:
        old = make_contract(make_field("status", type="string", enum_values=old_values))
        new = make_contract(make_field("status", type="string", enum_values=new_values))

        change = single_change(old, new)

        assert change.change_type is expected_change_type
        assert change.category is ChangeCategory.BREAKING


class TestNonBreakingChanges:
    def test_added_optional_field_is_non_breaking(self) -> None:
        old = make_contract(make_field("amount"))
        new = make_contract(
            make_field("amount"),
            make_field("merchant_city", type="string", nullable=True, required=False),
        )

        change = single_change(old, new)

        assert change.change_type is ChangeType.OPTIONAL_FIELD_ADDED
        assert change.category is ChangeCategory.NON_BREAKING

    def test_added_defaulted_non_nullable_field_is_non_breaking(self) -> None:
        old = make_contract(make_field("amount"))
        new = make_contract(
            make_field("amount"),
            make_field("category", type="string", nullable=False, required=False),
        )

        change = single_change(old, new)

        assert change.change_type is ChangeType.OPTIONAL_FIELD_ADDED

    def test_added_enum_value_is_non_breaking(self) -> None:
        old = make_contract(make_field("status", type="string", enum_values=("booked",)))
        new = make_contract(make_field("status", type="string", enum_values=("booked", "pending")))

        change = single_change(old, new)

        assert change.change_type is ChangeType.ENUM_VALUE_ADDED
        assert change.category is ChangeCategory.NON_BREAKING
        assert "pending" in change.detail

    def test_field_becoming_optional_is_non_breaking(self) -> None:
        old = make_contract(make_field("amount", required=True))
        new = make_contract(make_field("amount", required=False))

        change = single_change(old, new)

        assert change.change_type is ChangeType.FIELD_BECAME_OPTIONAL
        assert change.category is ChangeCategory.NON_BREAKING

    def test_reordered_fields_are_a_non_breaking_change(self) -> None:
        old = make_contract(make_field("amount"), make_field("currency", type="string"))
        new = make_contract(make_field("currency", type="string"), make_field("amount"))

        change = single_change(old, new)

        assert change.change_type is ChangeType.FIELDS_REORDERED
        assert change.category is ChangeCategory.NON_BREAKING
        assert required_bump([change]) is BumpLevel.MINOR

    def test_added_or_removed_fields_are_not_reported_as_reordering(self) -> None:
        old = make_contract(make_field("amount"), make_field("currency", type="string"))
        new = make_contract(
            make_field("amount"),
            make_field("note", type="string", nullable=True, required=False),
            make_field("currency", type="string"),
        )

        change_types = {change.change_type for change in diff_contracts(old, new)}

        assert ChangeType.FIELDS_REORDERED not in change_types
        assert change_types == {ChangeType.OPTIONAL_FIELD_ADDED}


class TestDocumentationChanges:
    def test_doc_change_is_documentation_level(self) -> None:
        old = make_contract(make_field("amount", doc="Signed amount"))
        new = make_contract(make_field("amount", doc="Signed amount in account currency"))

        change = single_change(old, new)

        assert change.change_type is ChangeType.DOC_CHANGED
        assert change.category is ChangeCategory.DOCUMENTATION


class TestDiffMechanics:
    def test_identical_contracts_produce_no_changes(self) -> None:
        assert diff_contracts(make_contract(), make_contract()) == []

    def test_multiple_changes_are_all_reported(self) -> None:
        old = make_contract(
            make_field("amount"),
            make_field("status", type="string", enum_values=("booked", "pending")),
        )
        new = make_contract(
            make_field("amount", type="string"),
            make_field("status", type="string", enum_values=("booked", "settled")),
        )

        change_types = {change.change_type for change in diff_contracts(old, new)}

        assert change_types == {
            ChangeType.TYPE_CHANGED,
            ChangeType.ENUM_VALUE_REMOVED,
            ChangeType.ENUM_VALUE_ADDED,
        }

    def test_diffing_different_subjects_is_an_error(self) -> None:
        other_subject = make_contract().model_copy(update={"subject": "landing_accounts"})

        with pytest.raises(ValueError, match="subject"):
            diff_contracts(make_contract(), other_subject)


class TestRequiredBump:
    def test_breaking_changes_require_a_major_bump(self) -> None:
        old = make_contract(make_field("amount"), make_field("note", type="string"))
        new = make_contract(
            make_field("amount", doc="reworded"),
            make_field("extra", type="string", nullable=True, required=False),
        )

        assert required_bump(diff_contracts(old, new)) is BumpLevel.MAJOR

    def test_non_breaking_changes_require_a_minor_bump(self) -> None:
        old = make_contract(make_field("amount"))
        new = make_contract(
            make_field("amount", doc="reworded"),
            make_field("extra", type="string", nullable=True, required=False),
        )

        assert required_bump(diff_contracts(old, new)) is BumpLevel.MINOR

    def test_doc_only_changes_require_a_patch_bump(self) -> None:
        old = make_contract(make_field("amount", doc="Signed amount"))
        new = make_contract(make_field("amount", doc="reworded"))

        assert required_bump(diff_contracts(old, new)) is BumpLevel.PATCH

    def test_no_changes_require_no_bump(self) -> None:
        assert required_bump([]) is BumpLevel.NONE


class TestVersionChangeProblems:
    def test_unchanged_version_with_no_changes_is_clean(self) -> None:
        assert version_change_problems("1.2.3", "1.2.3", BumpLevel.NONE) == []

    def test_bump_without_changes_is_a_problem(self) -> None:
        problems = version_change_problems("1.2.3", "1.2.4", BumpLevel.NONE)

        assert any("without" in problem for problem in problems)

    def test_major_bump_satisfies_breaking_changes(self) -> None:
        assert version_change_problems("1.2.3", "2.0.0", BumpLevel.MAJOR) == []

    def test_minor_bump_does_not_satisfy_breaking_changes(self) -> None:
        problems = version_change_problems("1.2.3", "1.3.0", BumpLevel.MAJOR)

        assert any("major" in problem for problem in problems)

    @pytest.mark.parametrize("new_version", ["1.1.0", "2.0.0"])
    def test_minor_or_major_bump_satisfies_non_breaking_changes(self, new_version: str) -> None:
        assert version_change_problems("1.0.5", new_version, BumpLevel.MINOR) == []

    def test_patch_bump_does_not_satisfy_non_breaking_changes(self) -> None:
        problems = version_change_problems("1.0.5", "1.0.6", BumpLevel.MINOR)

        assert any("minor" in problem for problem in problems)

    @pytest.mark.parametrize("new_version", ["1.0.1", "1.1.0", "2.0.0"])
    def test_any_bump_satisfies_doc_changes(self, new_version: str) -> None:
        assert version_change_problems("1.0.0", new_version, BumpLevel.PATCH) == []

    def test_unbumped_doc_changes_are_a_problem(self) -> None:
        assert version_change_problems("1.0.0", "1.0.0", BumpLevel.PATCH) != []

    def test_version_going_backwards_is_a_problem(self) -> None:
        problems = version_change_problems("1.2.3", "1.2.2", BumpLevel.PATCH)

        assert any("backwards" in problem for problem in problems)

    def test_malformed_version_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="semver"):
            parse_version("v1.0")
