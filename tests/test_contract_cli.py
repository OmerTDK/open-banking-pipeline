"""Tests for the contracts CLI: artifact generation, breaking-change detection, exit codes."""

import json
from pathlib import Path

import pytest

from open_banking_pipeline.contracts.cli import main
from open_banking_pipeline.contracts.generate import generate_all_contracts
from open_banking_pipeline.contracts.model import parse_contract

CHECK_OK = 0
CHECK_FAILED = 1


def generate_into(directory: Path) -> None:
    assert main(["generate", "--contracts-dir", str(directory)]) == CHECK_OK


def load_artifact(directory: Path, subject: str) -> dict:
    return json.loads((directory / f"{subject}.json").read_text(encoding="utf-8"))


def dump_artifact(directory: Path, subject: str, payload: dict) -> None:
    (directory / f"{subject}.json").write_text(json.dumps(payload), encoding="utf-8")


def write_categorizer_manifest(directory: Path, acknowledged_version: str) -> None:
    consumers_dir = directory / "consumers"
    consumers_dir.mkdir(exist_ok=True)
    manifest = {
        "consumer": "categorization_engine",
        "consumes": {
            "canonical_transaction": {
                "fields": ["transaction_id", "amount"],
                "acknowledged_version": acknowledged_version,
            }
        },
    }
    (consumers_dir / "categorization_engine.json").write_text(json.dumps(manifest))


@pytest.fixture
def contracts_dir(tmp_path: Path) -> Path:
    generated_dir = tmp_path / "contracts"
    generate_into(generated_dir)
    return generated_dir


class TestGenerate:
    def test_generate_writes_one_parseable_artifact_per_subject(self, contracts_dir: Path) -> None:
        derived = generate_all_contracts()

        artifact_names = sorted(path.name for path in contracts_dir.glob("*.json"))

        assert artifact_names == sorted(f"{subject}.json" for subject in derived)
        for subject, contract in derived.items():
            artifact_text = (contracts_dir / f"{subject}.json").read_text(encoding="utf-8")
            assert parse_contract(artifact_text) == contract

    def test_generate_twice_is_byte_identical(self, contracts_dir: Path, tmp_path: Path) -> None:
        second_dir = tmp_path / "second"
        generate_into(second_dir)

        for artifact_path in contracts_dir.glob("*.json"):
            assert artifact_path.read_bytes() == (second_dir / artifact_path.name).read_bytes()

    def test_generate_refuses_an_unbumped_breaking_overwrite(self, contracts_dir: Path) -> None:
        artifact = load_artifact(contracts_dir, "canonical_transaction")
        original_text = (contracts_dir / "canonical_transaction.json").read_text()
        artifact["fields"].append(
            {"name": "legacy_flag", "type": "boolean", "nullable": False, "required": True}
        )
        dump_artifact(contracts_dir, "canonical_transaction", artifact)

        exit_code = main(["generate", "--contracts-dir", str(contracts_dir)])

        assert exit_code == CHECK_FAILED
        assert (contracts_dir / "canonical_transaction.json").read_text() != original_text


class TestCheckExitCodes:
    def test_fresh_artifacts_pass(self, contracts_dir: Path) -> None:
        assert main(["check", "--contracts-dir", str(contracts_dir)]) == CHECK_OK

    def test_missing_artifacts_pass_as_new_contracts(self, tmp_path: Path) -> None:
        assert main(["check", "--contracts-dir", str(tmp_path / "absent")]) == CHECK_OK

    def test_simulated_breaking_change_fails_without_a_version_bump(
        self, contracts_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        artifact = load_artifact(contracts_dir, "canonical_transaction")
        artifact["fields"].append(
            {"name": "legacy_flag", "type": "boolean", "nullable": False, "required": True}
        )
        dump_artifact(contracts_dir, "canonical_transaction", artifact)

        exit_code = main(["check", "--contracts-dir", str(contracts_dir)])

        assert exit_code == CHECK_FAILED
        output = capsys.readouterr().out
        assert "field_removed" in output
        assert "breaking" in output
        assert "major" in output

    def test_breaking_change_passes_with_a_major_bump(self, contracts_dir: Path) -> None:
        artifact = load_artifact(contracts_dir, "canonical_transaction")
        artifact["version"] = "0.9.0"
        artifact["fields"].append(
            {"name": "legacy_flag", "type": "boolean", "nullable": False, "required": True}
        )
        dump_artifact(contracts_dir, "canonical_transaction", artifact)

        assert main(["check", "--contracts-dir", str(contracts_dir)]) == CHECK_OK

    def test_doc_change_fails_without_a_patch_bump(self, contracts_dir: Path) -> None:
        artifact = load_artifact(contracts_dir, "canonical_transaction")
        artifact["fields"][0]["doc"] = "an older wording"
        dump_artifact(contracts_dir, "canonical_transaction", artifact)

        assert main(["check", "--contracts-dir", str(contracts_dir)]) == CHECK_FAILED

    def test_reformatted_artifact_is_reported_stale(self, contracts_dir: Path) -> None:
        dump_artifact(
            contracts_dir,
            "canonical_transaction",
            load_artifact(contracts_dir, "canonical_transaction"),
        )

        assert main(["check", "--contracts-dir", str(contracts_dir)]) == CHECK_FAILED

    def test_removed_subject_fails(self, contracts_dir: Path) -> None:
        artifact = load_artifact(contracts_dir, "canonical_transaction")
        artifact["subject"] = "retired_subject"
        dump_artifact(contracts_dir, "retired_subject", artifact)

        assert main(["check", "--contracts-dir", str(contracts_dir)]) == CHECK_FAILED


class TestRequireFresh:
    def test_missing_artifacts_fail_when_freshness_is_required(self, tmp_path: Path) -> None:
        absent_dir = tmp_path / "absent"

        exit_code = main(["check", "--require-fresh", "--contracts-dir", str(absent_dir)])

        assert exit_code == CHECK_FAILED

    def test_properly_bumped_changes_still_fail_when_freshness_is_required(
        self, contracts_dir: Path
    ) -> None:
        artifact = load_artifact(contracts_dir, "canonical_transaction")
        artifact["version"] = "0.9.0"
        artifact["fields"].append(
            {"name": "legacy_flag", "type": "boolean", "nullable": False, "required": True}
        )
        dump_artifact(contracts_dir, "canonical_transaction", artifact)

        assert main(["check", "--contracts-dir", str(contracts_dir)]) == CHECK_OK
        exit_code = main(["check", "--require-fresh", "--contracts-dir", str(contracts_dir)])
        assert exit_code == CHECK_FAILED


class TestConsumerEnforcement:
    def make_acked_breaking_change(self, contracts_dir: Path) -> None:
        artifact = load_artifact(contracts_dir, "canonical_transaction")
        artifact["version"] = "0.9.0"
        for field in artifact["fields"]:
            if field["name"] == "amount":
                field["type"] = "string"
        dump_artifact(contracts_dir, "canonical_transaction", artifact)

    def test_breaking_change_to_consumed_field_fails_despite_major_bump(
        self, contracts_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self.make_acked_breaking_change(contracts_dir)
        write_categorizer_manifest(contracts_dir, acknowledged_version="0.9.0")

        exit_code = main(["check", "--contracts-dir", str(contracts_dir)])

        assert exit_code == CHECK_FAILED
        assert "categorization_engine" in capsys.readouterr().out

    def test_consumer_acknowledgement_clears_the_veto(self, contracts_dir: Path) -> None:
        self.make_acked_breaking_change(contracts_dir)
        write_categorizer_manifest(contracts_dir, acknowledged_version="1.0.0")

        assert main(["check", "--contracts-dir", str(contracts_dir)]) == CHECK_OK

    def test_manifest_pinning_a_missing_field_fails(self, contracts_dir: Path) -> None:
        consumers_dir = contracts_dir / "consumers"
        consumers_dir.mkdir()
        manifest = {
            "consumer": "categorization_engine",
            "consumes": {
                "canonical_transaction": {
                    "fields": ["vanished_field"],
                    "acknowledged_version": "1.0.0",
                }
            },
        }
        (consumers_dir / "categorization_engine.json").write_text(json.dumps(manifest))

        assert main(["check", "--contracts-dir", str(contracts_dir)]) == CHECK_FAILED


class TestDuplicateSubjects:
    def shadow_artifact(self, contracts_dir: Path) -> None:
        real_text = (contracts_dir / "canonical_transaction.json").read_text(encoding="utf-8")
        (contracts_dir / "zz_shadow.json").write_text(real_text, encoding="utf-8")

    def test_two_artifacts_declaring_one_subject_fail_loudly(
        self, contracts_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self.shadow_artifact(contracts_dir)

        exit_code = main(["check", "--require-fresh", "--contracts-dir", str(contracts_dir)])

        assert exit_code == CHECK_FAILED
        output = capsys.readouterr().out
        assert "canonical_transaction" in output
        assert "zz_shadow.json" in output
        assert "canonical_transaction.json" in output

    def test_a_fresh_shadow_cannot_mask_a_corrupted_artifact(self, contracts_dir: Path) -> None:
        self.shadow_artifact(contracts_dir)
        artifact = load_artifact(contracts_dir, "canonical_transaction")
        artifact["fields"].append(
            {"name": "legacy_flag", "type": "boolean", "nullable": False, "required": True}
        )
        dump_artifact(contracts_dir, "canonical_transaction", artifact)

        exit_code = main(["check", "--require-fresh", "--contracts-dir", str(contracts_dir)])

        assert exit_code == CHECK_FAILED

    def test_generate_refuses_duplicate_subjects(self, contracts_dir: Path) -> None:
        self.shadow_artifact(contracts_dir)

        assert main(["generate", "--contracts-dir", str(contracts_dir)]) == CHECK_FAILED


class TestRepositoryContracts:
    def test_committed_contracts_pass_the_strict_check(self) -> None:
        assert main(["check", "--require-fresh"]) == CHECK_OK
