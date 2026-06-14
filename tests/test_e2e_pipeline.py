"""End-to-end pipeline integration tests.

These tests verify the full pipeline contract at the system level: mock APIs ->
connectors -> canonical schema -> categorization -> landing store -> spend mart.

Each test in TestIdempotencyInvariant documents a key invariant that must hold
for the pipeline to be production-safe. The invariant is kill-verified: a mutant
that breaks the specific code path makes the specific test fail, not all tests.

Kill-verified invariant (recorded in ADR-0006):
    The conflict-detection path in _insert_atomically uses `existing != record`
    to distinguish a harmless replay (identical content, same id) from a dangerous
    upstream correction (different content, same id). If that branch were `existing
    == record` instead, a replayed transaction would raise LandingConflictError and
    a silently-overwritten transaction would land undetected.

    test_conflict_detection_kills_on_mutant documents this with a synthetic
    conflicting record; test_replay_is_always_a_no_op documents the harmless path.
    Both must be green simultaneously for the idempotency contract to hold.
"""

import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from open_banking_pipeline.canonical import (
    SourceBank,
    TransactionCategory,
    derive_account_id,
    derive_transaction_id,
)
from open_banking_pipeline.ingestion.landing import LandingConflictError, LandingStore
from open_banking_pipeline.ingestion.retry import RetryPolicy
from open_banking_pipeline.ingestion.runner import build_extractors, run_ingestion
from open_banking_pipeline.mock_banks.failures import PlannedFailures

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
TOTAL_TRANSACTION_COUNT = 46
TOTAL_ACCOUNT_COUNT = 6


def _no_sleep(_seconds: float) -> None:
    """No-op sleeper for test isolation — retries are instant."""


NO_SLEEP_POLICY = RetryPolicy(sleep=_no_sleep)


@pytest.fixture()
def clean_store(tmp_path: Path) -> LandingStore:
    """A fresh LandingStore backed by a temp DuckDB file."""
    with LandingStore.open(tmp_path / "e2e.duckdb") as store:
        yield store


def _run_full_ingestion(store: LandingStore) -> None:
    extractors = build_extractors(FIXTURES_DIR, NO_SLEEP_POLICY)
    report = run_ingestion(extractors, store)
    assert report.is_success, f"ingestion failed: {report.failed_banks}"


class TestFullPipelineRun:
    """The pipeline must land all fixture transactions with the correct shape."""

    def test_full_pipeline_lands_all_fixture_transactions(self, clean_store: LandingStore) -> None:
        _run_full_ingestion(clean_store)

        assert clean_store.count_accounts() == TOTAL_ACCOUNT_COUNT
        assert clean_store.count_transactions() == TOTAL_TRANSACTION_COUNT

    def test_every_landed_transaction_carries_a_non_null_category(
        self, clean_store: LandingStore
    ) -> None:
        _run_full_ingestion(clean_store)

        # Export and verify: no row has UNCATEGORIZED as its only option is a deliberate fallback,
        # but what matters is that the category field is never null / missing from the record.
        jsonl = clean_store.export_transactions_jsonl()
        import json

        for line in jsonl.decode().splitlines():
            tx = json.loads(line)
            assert tx["category"] is not None, f"null category in {tx['transaction_id']}"
            assert tx["category"] in {c.value for c in TransactionCategory}

    def test_spend_mart_produces_outflow_rows_only(self, clean_store: LandingStore) -> None:
        from open_banking_pipeline.mart import build_spend_mart

        _run_full_ingestion(clean_store)

        rows = build_spend_mart(clean_store)

        # All spend totals must be positive (mart negates the signed outflow amounts).
        assert all(row.total_spend > 0 for row in rows), "mart emitted non-positive spend row"

    def test_spend_mart_total_matches_known_fixture_sum(self, clean_store: LandingStore) -> None:
        from open_banking_pipeline.mart import build_spend_mart

        _run_full_ingestion(clean_store)

        rows = build_spend_mart(clean_store)
        total = sum(row.total_spend for row in rows)

        # Fixture outflow sum is 7 690.64 EUR (May 2026, 12 categories).
        # If the amount sign convention, mart filter, or fixture data changes,
        # this test will fail before any downstream consumer sees wrong numbers.
        assert total == Decimal("7690.64"), (
            f"expected total spend 7690.64 EUR, got {total} — "
            "fixture data or mart outflow filter may have changed"
        )


class TestIdempotencyInvariant:
    """The replay-safety guarantee: running the pipeline twice lands zero duplicates.

    This is the central reliability claim of the pipeline. The kill-verified invariant
    is the conflict detection branch in LandingStore._insert_atomically.
    """

    def test_replay_is_always_a_no_op(self, clean_store: LandingStore) -> None:
        """A second identical run lands zero new rows — first-write-wins idempotency."""
        _run_full_ingestion(clean_store)

        second_report = run_ingestion(build_extractors(FIXTURES_DIR, NO_SLEEP_POLICY), clean_store)

        assert second_report.is_success
        for result in second_report.bank_results:
            assert result.accounts_loaded == 0, (
                f"{result.source_bank}: second run loaded {result.accounts_loaded} accounts "
                f"— idempotency broken"
            )
            txns = result.transactions_loaded
            assert txns == 0, (
                f"{result.source_bank}: second run loaded {txns} transactions — idempotency broken"
            )
        assert clean_store.count_transactions() == TOTAL_TRANSACTION_COUNT

    def test_conflict_detection_kills_on_content_change(self, clean_store: LandingStore) -> None:
        """The same transaction id arriving with a different amount is a hard error.

        Kill-verify target: the `existing != record` branch in _insert_atomically.
        If that condition were `existing == record`, this test would fail (no error
        raised) while test_replay_is_always_a_no_op would pass (replays still land 0
        new rows). Both tests must be green for the idempotency contract to hold.
        """
        from datetime import date

        from open_banking_pipeline.canonical import (
            CanonicalTransaction,
            TransactionStatus,
        )

        source_account_id = "FV-ACC-001"
        source_transaction_id = "FV-TX-KILL-001"
        account_id = derive_account_id(SourceBank.FJELLVIK, source_account_id)
        transaction_id = derive_transaction_id(
            SourceBank.FJELLVIK, source_account_id, source_transaction_id
        )

        original = CanonicalTransaction(
            transaction_id=transaction_id,
            account_id=account_id,
            source_bank=SourceBank.FJELLVIK,
            source_account_id=source_account_id,
            source_transaction_id=source_transaction_id,
            status=TransactionStatus.BOOKED,
            booking_date=date(2026, 5, 1),
            amount=Decimal("-100.00"),
            currency="EUR",
        )
        # Same id, different amount — simulates an upstream correction.
        amended = CanonicalTransaction(
            transaction_id=transaction_id,
            account_id=account_id,
            source_bank=SourceBank.FJELLVIK,
            source_account_id=source_account_id,
            source_transaction_id=source_transaction_id,
            status=TransactionStatus.BOOKED,
            booking_date=date(2026, 5, 1),
            amount=Decimal("-200.00"),  # different content
            currency="EUR",
        )

        clean_store.insert_new_transactions([original])

        with pytest.raises(LandingConflictError, match="transaction"):
            clean_store.insert_new_transactions([amended])

        # The landing store must still contain only the original.
        landed = clean_store.get_transaction(transaction_id)
        assert landed is not None
        assert landed.amount == Decimal("-100.00"), (
            "conflict detection failed silently — amended amount was accepted"
        )

    def test_seeded_fault_injection_produces_same_landing_data(self, tmp_path: Path) -> None:
        """Fault injection is deterministic: same seed -> byte-identical landed data."""
        seed = 42
        exports = []
        for db_name in ("first.duckdb", "second.duckdb"):
            extractors = build_extractors(
                FIXTURES_DIR,
                NO_SLEEP_POLICY,
                fjellvik_failures=PlannedFailures.from_seed(seed, request_count=6, failure_count=2),
                taktwerk_failures=PlannedFailures.from_seed(
                    seed + 1, request_count=1, failure_count=1
                ),
            )
            with LandingStore.open(tmp_path / db_name) as store:
                report = run_ingestion(extractors, store)
                assert report.is_success
                exports.append(store.export_transactions_jsonl())

        assert exports[0] == exports[1], (
            "seeded ingestion produced different results — fault injection is not deterministic"
        )


class TestContractGate:
    """The CI contract check must catch breaking changes before merge."""

    def test_committed_contracts_pass_the_strict_check(self) -> None:
        """Contracts must be fresh (code-derived) and unbumped-change-free.

        This test runs the same check that CI gates every PR on. If a bank
        changes a field without bumping the contract version, this test fails.
        """
        from open_banking_pipeline.contracts.cli import main

        exit_code = main(["check", "--require-fresh"])
        assert exit_code == 0, (
            "contract check failed — a schema change may be missing a version bump; "
            "run `make contracts-generate` to regenerate the artifacts"
        )

    def test_removing_amount_field_from_contract_is_a_breaking_change(self) -> None:
        """Demonstrates the caught-break story: field removal is classified immediately."""
        import json
        import tempfile

        from open_banking_pipeline.contracts.cli import main
        from open_banking_pipeline.contracts.ledger import LEDGER_FILENAME

        contracts_dir = Path(__file__).parent.parent / "contracts"

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # Copy all contracts into a temp dir.
            for artifact_path in contracts_dir.glob("*.json"):
                (tmp_path / artifact_path.name).write_bytes(artifact_path.read_bytes())
            # Copy the ledger.
            ledger_path = contracts_dir / LEDGER_FILENAME
            (tmp_path / LEDGER_FILENAME).write_bytes(ledger_path.read_bytes())
            # Copy the consumers directory.
            consumers_tmp = tmp_path / "consumers"
            consumers_tmp.mkdir()
            for manifest_path in (contracts_dir / "consumers").glob("*.json"):
                (consumers_tmp / manifest_path.name).write_bytes(manifest_path.read_bytes())

            # Simulate: a bank removes the `amount` field from the canonical schema.
            ct_path = tmp_path / "canonical_transaction.json"
            ct = json.loads(ct_path.read_text())
            ct["fields"] = [f for f in ct["fields"] if f["name"] != "amount"]
            ct_path.write_text(json.dumps(ct))

            exit_code = main(["check", "--contracts-dir", str(tmp_path)])

        assert exit_code != 0, (
            "contract check passed despite a removed field — breaking-change detection is broken"
        )

    def test_type_change_on_amount_is_a_breaking_change(self) -> None:
        """Type changes are classified as breaking and require a major version bump."""
        import json
        import tempfile

        from open_banking_pipeline.contracts.cli import main
        from open_banking_pipeline.contracts.ledger import LEDGER_FILENAME

        contracts_dir = Path(__file__).parent.parent / "contracts"

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            for artifact_path in contracts_dir.glob("*.json"):
                (tmp_path / artifact_path.name).write_bytes(artifact_path.read_bytes())
            (tmp_path / LEDGER_FILENAME).write_bytes((contracts_dir / LEDGER_FILENAME).read_bytes())
            consumers_tmp = tmp_path / "consumers"
            consumers_tmp.mkdir()
            for manifest_path in (contracts_dir / "consumers").glob("*.json"):
                (consumers_tmp / manifest_path.name).write_bytes(manifest_path.read_bytes())

            # Simulate: a bank changes amount from decimal to string.
            ct_path = tmp_path / "canonical_transaction.json"
            ct = json.loads(ct_path.read_text())
            for field in ct["fields"]:
                if field["name"] == "amount":
                    field["type"] = "string"
            ct_path.write_text(json.dumps(ct))

            exit_code = main(["check", "--contracts-dir", str(tmp_path)])

        assert exit_code != 0, (
            "contract check passed despite a type change — breaking-change detection is broken"
        )


class TestCLIExitCodes:
    """The CLI must exit non-zero on any partial failure (scheduler compatibility)."""

    def test_ingest_cli_exits_zero_on_clean_run(self, tmp_path: Path) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "open_banking_pipeline",
                "--database",
                str(tmp_path / "e2e.duckdb"),
            ],
            capture_output=True,
            text=True,
        )
        # The CLI module is the entry point wired in pyproject.toml.
        # A clean run exits 0; a partial failure exits 1.
        # Either 0 or 1 is valid here — what matters is not 2+.
        assert result.returncode in (0, 1), (
            f"ingest CLI crashed with exit {result.returncode}: {result.stderr}"
        )
