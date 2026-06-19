"""Spend-by-category-by-month consumption mart (ADR-0005).

Queries the DuckDB landing store to produce a monthly spend summary:
outflow transactions only (amount < 0), grouped by booking month and
canonical category, ordered by year, month, total_spend descending.

This mart is the first declared consumer of the categorized canonical
transaction layer.  Its consumer manifest is committed under
``contracts/consumers/spend_mart.json``.

Grain: one row per (calendar year, calendar month, TransactionCategory).
Inflows (amount >= 0) are excluded from the outflow-spend view.
"""

from dataclasses import dataclass
from decimal import Decimal

from open_banking_pipeline.canonical import TransactionCategory
from open_banking_pipeline.ingestion.landing import LandingStore

_SPEND_MART_QUERY = """
SELECT
    YEAR(booking_date)                  AS year,
    MONTH(booking_date)                 AS month,
    category,
    SUM(ABS(amount))                    AS total_spend,
    COUNT(*)                            AS transaction_count
FROM transactions
WHERE amount < 0
  AND booking_date IS NOT NULL
GROUP BY
    YEAR(booking_date),
    MONTH(booking_date),
    category
ORDER BY
    YEAR(booking_date),
    MONTH(booking_date),
    SUM(ABS(amount)) DESC
"""


@dataclass(frozen=True)
class SpendRow:
    """One aggregated row of the spend mart.

    Attributes:
        year: Calendar year of the booking month (e.g. 2026).
        month: Calendar month number 1-12 (e.g. 5 for May).
        category: Canonical spend category.
        total_spend: Sum of absolute outflow amounts in EUR; always positive.
        transaction_count: Number of outflow transactions in this bucket.
    """

    year: int
    month: int
    category: TransactionCategory
    total_spend: Decimal
    transaction_count: int


def build_spend_mart(store: LandingStore) -> list[SpendRow]:
    """Query the landing store and return the spend-by-category-by-month mart.

    Args:
        store: An open LandingStore whose transactions table is populated.

    Returns:
        Ordered list of SpendRow — one per (year, month, category) bucket with
        at least one outflow transaction.  Empty when no outflows are present.
    """
    rows = store._connection.execute(_SPEND_MART_QUERY).fetchall()
    return [
        SpendRow(
            year=int(row[0]),
            month=int(row[1]),
            category=TransactionCategory(row[2]),
            total_spend=Decimal(str(row[3])),
            transaction_count=int(row[4]),
        )
        for row in rows
    ]
