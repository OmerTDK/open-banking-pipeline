"""Append-only subjects ledger: the last recorded version of every contracted subject.

The committed artifacts alone are a forgeable baseline: delete one and the
sanctioned ``generate`` command would recreate it from current code at the
same version, shipping a breaking change without a bump. The ledger anchors
history — once a subject is recorded, a committed artifact must exist at a
version no lower than the recorded one, and ``generate`` only ever moves
recorded versions forward (ADR-0004).
"""

import json
from pathlib import Path

from open_banking_pipeline.contracts.diff import parse_version

LEDGER_FILENAME = "_subjects_ledger.json"
JSON_INDENT = 2


def load_ledger(contracts_dir: Path) -> dict[str, str] | None:
    """Return the recorded subject -> version map, or None when no ledger exists."""
    ledger_path = contracts_dir / LEDGER_FILENAME
    if not ledger_path.is_file():
        return None
    loaded = json.loads(ledger_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"{LEDGER_FILENAME} must be an object mapping subject names to versions")
    for version in loaded.values():
        parse_version(version)
    return loaded


def serialize_ledger(ledger: dict[str, str]) -> str:
    """Serialize the ledger to canonical JSON (deterministic byte-for-byte)."""
    return json.dumps(ledger, indent=JSON_INDENT, sort_keys=True) + "\n"


def vanished_recorded_subjects(
    ledger: dict[str, str] | None,
    committed_versions: dict[str, str],
) -> list[str]:
    """Return the recorded subjects whose committed artifact is gone."""
    return sorted(set(ledger or {}) - set(committed_versions))


def ledger_problems(
    ledger: dict[str, str] | None,
    committed_versions: dict[str, str],
) -> list[str]:
    """Return why the committed artifacts contradict the recorded history (empty = OK)."""
    if ledger is None:
        if not committed_versions:
            return []
        return [
            "the subjects ledger is missing while committed artifacts exist; "
            "run `make contracts-generate` to record the baseline"
        ]
    problems = [
        f"{subject}: recorded in the subjects ledger at {ledger[subject]} but the committed "
        f"artifact is gone; recorded subjects never vanish — restore the artifact from git"
        for subject in vanished_recorded_subjects(ledger, committed_versions)
    ]
    problems.extend(
        f"{subject}: committed artifact is not recorded in the subjects ledger; "
        f"run `make contracts-generate` to record it"
        for subject in sorted(set(committed_versions) - set(ledger))
    )
    problems.extend(
        f"{subject}: committed artifact at {committed_versions[subject]} is behind the "
        f"recorded version {ledger[subject]}; recorded versions never rewind"
        for subject in sorted(set(ledger) & set(committed_versions))
        if parse_version(committed_versions[subject]) < parse_version(ledger[subject])
    )
    return problems


def merged_ledger(
    ledger: dict[str, str] | None,
    current_versions: dict[str, str],
) -> dict[str, str]:
    """Fold the current subject versions into the ledger, only ever moving forward."""
    merged = dict(ledger or {})
    for subject, version in current_versions.items():
        recorded = merged.get(subject)
        if recorded is None or parse_version(version) > parse_version(recorded):
            merged[subject] = version
    return merged
