"""Command-line entry point for the data contracts: generate artifacts, detect breaks.

``check`` diffs the code-derived contracts against the committed artifacts and
exits non-zero on any breaking change that is not covered by a major version
bump, any change without the bump its classification demands, and any
unacknowledged breaking change to a field a consumer manifest pins.
``--require-fresh`` (the CI mode) additionally fails while the committed
artifacts differ from code at all, so merged state always carries regenerated
artifacts. ``generate`` refuses to overwrite artifacts when the pending
changes are incompatible — bump the version in ``versions.py`` first.
"""

import argparse
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from open_banking_pipeline.contracts.consumers import (
    consumer_veto_problems,
    load_consumer_manifests,
    manifest_problems,
)
from open_banking_pipeline.contracts.diff import (
    ContractChange,
    diff_contracts,
    required_bump,
    version_change_problems,
)
from open_banking_pipeline.contracts.generate import generate_all_contracts
from open_banking_pipeline.contracts.model import Contract, parse_contract, serialize_contract

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONTRACTS_DIR = REPOSITORY_ROOT / "contracts"
CONSUMERS_SUBDIRECTORY = "consumers"

EXIT_COMPATIBLE = 0
EXIT_INCOMPATIBLE = 1


class DuplicateSubjectError(Exception):
    """Two committed artifact files declare the same subject; refusing to pick one."""


@dataclass(frozen=True)
class Assessment:
    """Everything `check` and `generate` need to know about code vs committed state."""

    changes: list[ContractChange]
    notices: list[str]
    compatibility_problems: list[str]
    stale_subjects: list[str]
    missing_subjects: list[str]
    format_drift_subjects: list[str]


def main(argv: list[str] | None = None) -> int:
    """Run one contracts command; return 0 when the contracts are compatible."""
    arguments = _parse_arguments(argv)
    derived = generate_all_contracts()
    try:
        assessment = assess_contracts(derived, arguments.contracts_dir)
    except DuplicateSubjectError as error:
        print(f"PROBLEM: {error}")
        return EXIT_INCOMPATIBLE
    if arguments.command == "generate":
        return _generate(derived, assessment, arguments.contracts_dir)
    return _check(assessment, require_fresh=arguments.require_fresh)


def assess_contracts(derived: dict[str, Contract], contracts_dir: Path) -> Assessment:
    """Diff the code-derived contracts against the committed artifacts and manifests."""
    committed = _load_committed_contracts(contracts_dir)
    drift = _merge_assessments(
        _assess_subject(subject, new_contract, committed.get(subject))
        for subject, new_contract in sorted(derived.items())
    )
    problems = [
        *_removed_subject_problems(derived, committed),
        *drift.compatibility_problems,
        *_consumer_problems(derived, drift.changes, contracts_dir),
    ]
    return Assessment(
        drift.changes,
        drift.notices,
        problems,
        drift.stale_subjects,
        drift.missing_subjects,
        drift.format_drift_subjects,
    )


def _assess_subject(
    subject: str,
    new_contract: Contract,
    committed_entry: tuple[Contract, str] | None,
) -> Assessment:
    if committed_entry is None:
        return Assessment(
            changes=[],
            notices=[f"{subject}: new contract at version {new_contract.version}"],
            compatibility_problems=[],
            stale_subjects=[],
            missing_subjects=[subject],
            format_drift_subjects=[],
        )
    old_contract, committed_text = committed_entry
    changes = diff_contracts(old_contract, new_contract)
    problems = [
        f"{subject}: {problem}"
        for problem in version_change_problems(
            old_contract.version, new_contract.version, required_bump(changes)
        )
    ]
    is_stale = serialize_contract(new_contract) != committed_text
    is_format_drift = is_stale and not changes and old_contract.version == new_contract.version
    return Assessment(
        changes=changes,
        notices=[],
        compatibility_problems=problems,
        stale_subjects=[subject] if is_stale else [],
        missing_subjects=[],
        format_drift_subjects=[subject] if is_format_drift else [],
    )


def _merge_assessments(assessments: Iterable[Assessment]) -> Assessment:
    merged = list(assessments)
    return Assessment(
        changes=[change for assessment in merged for change in assessment.changes],
        notices=[notice for assessment in merged for notice in assessment.notices],
        compatibility_problems=[
            problem for assessment in merged for problem in assessment.compatibility_problems
        ],
        stale_subjects=[subject for assessment in merged for subject in assessment.stale_subjects],
        missing_subjects=[
            subject for assessment in merged for subject in assessment.missing_subjects
        ],
        format_drift_subjects=[
            subject for assessment in merged for subject in assessment.format_drift_subjects
        ],
    )


def _removed_subject_problems(
    derived: dict[str, Contract],
    committed: dict[str, tuple[Contract, str]],
) -> list[str]:
    return [
        f"{subject}: a committed contract exists but the subject is gone from code; "
        f"removing a subject breaks every consumer"
        for subject in sorted(set(committed) - set(derived))
    ]


def _consumer_problems(
    derived: dict[str, Contract],
    changes: list[ContractChange],
    contracts_dir: Path,
) -> list[str]:
    manifests = load_consumer_manifests(contracts_dir / CONSUMERS_SUBDIRECTORY)
    problems = [
        problem for manifest in manifests for problem in manifest_problems(manifest, derived)
    ]
    new_versions = {subject: contract.version for subject, contract in derived.items()}
    return [*problems, *consumer_veto_problems(changes, manifests, new_versions)]


def _check(assessment: Assessment, require_fresh: bool) -> int:
    for notice in assessment.notices:
        print(notice)
    for change in assessment.changes:
        print(
            f"{change.subject}.{change.field_name} [{change.category.value}] "
            f"{change.change_type.value}: {change.detail}"
        )

    problems = list(assessment.compatibility_problems)
    if require_fresh:
        problems.extend(
            f"{subject}: committed artifact does not match the code-derived contract; "
            f"run `make contracts-generate` and commit the result"
            for subject in assessment.stale_subjects + assessment.missing_subjects
        )
    else:
        problems.extend(
            f"{subject}: committed artifact is not the canonical serialization; "
            f"run `make contracts-generate`"
            for subject in assessment.format_drift_subjects
        )

    for problem in problems:
        print(f"PROBLEM: {problem}")
    if problems:
        print("contracts check: FAILED")
        return EXIT_INCOMPATIBLE
    print("contracts check: OK")
    return EXIT_COMPATIBLE


def _generate(
    derived: dict[str, Contract],
    assessment: Assessment,
    contracts_dir: Path,
) -> int:
    if assessment.compatibility_problems:
        for problem in assessment.compatibility_problems:
            print(f"PROBLEM: {problem}")
        print("refusing to regenerate artifacts over incompatible changes")
        return EXIT_INCOMPATIBLE
    contracts_dir.mkdir(parents=True, exist_ok=True)
    for subject, contract in sorted(derived.items()):
        artifact_path = contracts_dir / f"{subject}.json"
        artifact_path.write_text(serialize_contract(contract), encoding="utf-8")
        print(f"wrote {artifact_path}")
    return EXIT_COMPATIBLE


def _load_committed_contracts(contracts_dir: Path) -> dict[str, tuple[Contract, str]]:
    if not contracts_dir.is_dir():
        return {}
    committed = {}
    artifact_paths: dict[str, Path] = {}
    for artifact_path in sorted(contracts_dir.glob("*.json")):
        artifact_text = artifact_path.read_text(encoding="utf-8")
        contract = parse_contract(artifact_text)
        if contract.subject in committed:
            raise DuplicateSubjectError(
                f"{artifact_path.name} declares subject {contract.subject!r}, which "
                f"{artifact_paths[contract.subject].name} already declares; the baseline "
                f"is ambiguous — remove one of the two files"
            )
        artifact_paths[contract.subject] = artifact_path
        committed[contract.subject] = (contract, artifact_text)
    return committed


def _parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="open-banking-contracts",
        description="Generate data-contract artifacts and detect breaking changes.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate_parser = subparsers.add_parser(
        "generate", help="write the code-derived contracts as committed artifacts"
    )
    check_parser = subparsers.add_parser(
        "check", help="diff code-derived contracts against the committed artifacts"
    )
    for subparser in (generate_parser, check_parser):
        subparser.add_argument(
            "--contracts-dir",
            type=Path,
            default=DEFAULT_CONTRACTS_DIR,
            help=f"directory of committed contract artifacts (default: {DEFAULT_CONTRACTS_DIR})",
        )
    check_parser.add_argument(
        "--require-fresh",
        action="store_true",
        help="also fail while committed artifacts differ from code at all (CI mode)",
    )
    return parser.parse_args(argv)
