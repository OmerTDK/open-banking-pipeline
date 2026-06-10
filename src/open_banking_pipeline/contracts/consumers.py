"""Consumer manifests: downstream consumers pin the contract fields they read.

A breaking change to a pinned field is a hard failure even when the producer
bumped the contract's major version — unless the consumer's manifest
acknowledges the new version, which proves the two sides moved together in
the same change set.
"""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from open_banking_pipeline.contracts.diff import ChangeCategory, ContractChange, parse_version
from open_banking_pipeline.contracts.model import SEMVER_PATTERN, Contract


class ConsumedSubject(BaseModel):
    """The fields one consumer reads from one contract subject."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fields: tuple[str, ...] = Field(min_length=1)
    acknowledged_version: str = Field(pattern=SEMVER_PATTERN)


class ConsumerManifest(BaseModel):
    """One downstream consumer's declared dependencies on contract subjects."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    consumer: str = Field(min_length=1)
    consumes: dict[str, ConsumedSubject] = Field(min_length=1)


def parse_consumer_manifest(text: str) -> ConsumerManifest:
    """Parse and validate a serialized consumer manifest; unknown keys are rejected."""
    return ConsumerManifest.model_validate_json(text)


def load_consumer_manifests(directory: Path) -> list[ConsumerManifest]:
    """Load every ``*.json`` manifest in ``directory``, sorted by file name."""
    if not directory.is_dir():
        return []
    return [
        parse_consumer_manifest(manifest_path.read_text(encoding="utf-8"))
        for manifest_path in sorted(directory.glob("*.json"))
    ]


def manifest_problems(
    manifest: ConsumerManifest,
    current_contracts: dict[str, Contract],
) -> list[str]:
    """Return why the manifest does not fit the current contracts (empty = OK)."""
    problems = []
    for subject, consumed in manifest.consumes.items():
        contract = current_contracts.get(subject)
        if contract is None:
            problems.append(f"consumer {manifest.consumer!r} consumes unknown subject {subject!r}")
            continue
        contract_field_names = {field.name for field in contract.fields}
        for field_name in consumed.fields:
            if field_name not in contract_field_names:
                problems.append(
                    f"consumer {manifest.consumer!r} pins {subject}.{field_name}, "
                    f"which does not exist in the current contract"
                )
        if parse_version(consumed.acknowledged_version) > parse_version(contract.version):
            problems.append(
                f"consumer {manifest.consumer!r} acknowledges {subject} version "
                f"{consumed.acknowledged_version}, which is ahead of the current "
                f"contract version {contract.version}"
            )
    return problems


def consumer_veto_problems(
    changes: list[ContractChange],
    manifests: list[ConsumerManifest],
    new_versions: dict[str, str],
) -> list[str]:
    """Return every unacknowledged breaking change to a consumed field (empty = OK)."""
    problems = []
    breaking_changes = [change for change in changes if change.category is ChangeCategory.BREAKING]
    for change in breaking_changes:
        for manifest in manifests:
            consumed = manifest.consumes.get(change.subject)
            if consumed is None or change.field_name not in consumed.fields:
                continue
            new_version = new_versions[change.subject]
            if parse_version(consumed.acknowledged_version) < parse_version(new_version):
                problems.append(
                    f"breaking change to consumed field {change.subject}.{change.field_name} "
                    f"({change.change_type.value}): consumer {manifest.consumer!r} has not "
                    f"acknowledged version {new_version} "
                    f"(acknowledged: {consumed.acknowledged_version})"
                )
    return problems
