"""Command-line entry point: ingest all three mock banks into the landing store.

``--failure-seed`` turns on deterministic fault injection (planned 429s for
fjellvik, one truncated download for taktwerk) so the retry path is part of
the demo; the landed data is byte-identical with or without it.
"""

import argparse
import time
from collections.abc import Callable
from pathlib import Path

from open_banking_pipeline.ingestion.landing import LandingStore
from open_banking_pipeline.ingestion.retry import RetryPolicy
from open_banking_pipeline.ingestion.runner import (
    IngestionReport,
    build_extractors,
    run_ingestion,
)
from open_banking_pipeline.mock_banks.failures import PlannedFailures

REPOSITORY_ROOT = Path(__file__).parent.parent.parent
DEFAULT_FIXTURES_DIR = REPOSITORY_ROOT / "fixtures"
DEFAULT_DATABASE_PATH = REPOSITORY_ROOT / "data" / "local" / "landing.duckdb"

FJELLVIK_PLANNED_REQUEST_COUNT = 6
FJELLVIK_PLANNED_FAILURE_COUNT = 2
TAKTWERK_PLANNED_REQUEST_COUNT = 1
TAKTWERK_PLANNED_FAILURE_COUNT = 1
# Offsetting taktwerk's seed decorrelates its failure schedule from fjellvik's.
TAKTWERK_FAILURE_SEED_OFFSET = 1

BANK_NAME_COLUMN_WIDTH = 10


def main(
    argv: list[str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Run one full ingestion; return 0 when every bank landed.

    Args:
        argv: Command-line arguments, or ``None`` for ``sys.argv``.
        sleep: Wait function for retry backoff; tests inject a no-op.
    """
    arguments = _parse_arguments(argv)
    fjellvik_failures, taktwerk_failures = _planned_failures(arguments.failure_seed)
    extractors = build_extractors(
        arguments.fixtures_dir,
        RetryPolicy(sleep=sleep),
        fjellvik_failures=fjellvik_failures,
        taktwerk_failures=taktwerk_failures,
    )
    with LandingStore.open(arguments.database) as store:
        report = run_ingestion(extractors, store)
        for line in _report_lines(report, store, arguments.database):
            print(line)
    return exit_code_for(report)


def exit_code_for(report: IngestionReport) -> int:
    return 0 if report.is_success else 1


def _parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="open-banking-ingest",
        description="Ingest all mock banks into the DuckDB landing store, idempotently.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help=f"DuckDB landing store path (default: {DEFAULT_DATABASE_PATH})",
    )
    parser.add_argument(
        "--fixtures-dir",
        type=Path,
        default=DEFAULT_FIXTURES_DIR,
        help=f"directory containing the bank fixtures (default: {DEFAULT_FIXTURES_DIR})",
    )
    parser.add_argument(
        "--failure-seed",
        type=int,
        default=None,
        help="inject deterministic 429s and a truncated download from this seed",
    )
    return parser.parse_args(argv)


def _planned_failures(
    failure_seed: int | None,
) -> tuple[PlannedFailures | None, PlannedFailures | None]:
    if failure_seed is None:
        return None, None
    fjellvik_failures = PlannedFailures.from_seed(
        failure_seed, FJELLVIK_PLANNED_REQUEST_COUNT, FJELLVIK_PLANNED_FAILURE_COUNT
    )
    taktwerk_failures = PlannedFailures.from_seed(
        failure_seed + TAKTWERK_FAILURE_SEED_OFFSET,
        TAKTWERK_PLANNED_REQUEST_COUNT,
        TAKTWERK_PLANNED_FAILURE_COUNT,
    )
    return fjellvik_failures, taktwerk_failures


def _report_lines(
    report: IngestionReport,
    store: LandingStore,
    database_path: Path,
) -> list[str]:
    lines = []
    for result in report.bank_results:
        bank_name = result.source_bank.value.ljust(BANK_NAME_COLUMN_WIDTH)
        if result.is_success:
            lines.append(
                f"{bank_name} accounts +{result.accounts_loaded}  "
                f"transactions +{result.transactions_loaded}"
            )
        else:
            lines.append(f"{bank_name} FAILED: {result.failure_reason}")
    lines.append(
        f"landing store: {store.count_accounts()} accounts, "
        f"{store.count_transactions()} transactions ({database_path})"
    )
    return lines
