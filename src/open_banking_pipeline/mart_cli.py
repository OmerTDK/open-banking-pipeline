"""Command-line entry point: print the spend-by-category-by-month mart.

Reads from an existing landing store and prints a formatted table showing
outflow spend aggregated by canonical category and booking month.
"""

import argparse
from pathlib import Path

from open_banking_pipeline.ingestion.landing import LandingStore
from open_banking_pipeline.mart import SpendRow, build_spend_mart

REPOSITORY_ROOT = Path(__file__).parent.parent.parent
DEFAULT_DATABASE_PATH = REPOSITORY_ROOT / "data" / "local" / "landing.duckdb"

MONTH_NAMES = [
    "",
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]


def main(argv: list[str] | None = None) -> int:
    """Print the spend mart; return 0 on success.

    Args:
        argv: Command-line arguments, or ``None`` for ``sys.argv``.
    """
    arguments = _parse_arguments(argv)
    with LandingStore.open(arguments.database) as store:
        rows = build_spend_mart(store)
    for line in _format_rows(rows):
        print(line)
    return 0


def _parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="open-banking-mart",
        description="Print spend by category by month from the landing store.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help=f"DuckDB landing store path (default: {DEFAULT_DATABASE_PATH})",
    )
    return parser.parse_args(argv)


def _format_rows(rows: list[SpendRow]) -> list[str]:
    if not rows:
        return ["No outflow transactions found."]
    header = f"{'Month':<10} {'Category':<20} {'Spend (EUR)':>12} {'Txns':>6}"
    separator = "-" * len(header)
    lines = [header, separator]
    for row in rows:
        month_label = f"{MONTH_NAMES[row.month]} {row.year}"
        spend = f"{row.total_spend:>12.2f}"
        count = f"{row.transaction_count:>6}"
        lines.append(f"{month_label:<10} {row.category.value:<20} {spend} {count}")
    lines.append(separator)
    total = sum(r.total_spend for r in rows)
    lines.append(f"{'Total':<32} {total:>12.2f}")
    return lines
