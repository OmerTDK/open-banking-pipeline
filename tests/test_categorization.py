"""Tests for the deterministic rule-based transaction categorizer.

Rules are first-match; precedence is documented in ADR-0005.
Tests cover: every category has at least one positive case, signal overlap is
resolved correctly by precedence, uncategorized is the terminal fallback, and
the categorizer is idempotent and pure (no side effects).
"""

import pytest

from open_banking_pipeline.canonical import TransactionCategory
from open_banking_pipeline.categorization import categorize, categorize_many

G = TransactionCategory.GROCERIES
D = TransactionCategory.DINING
T = TransactionCategory.TRANSPORT
U = TransactionCategory.UTILITIES
R = TransactionCategory.RENT
S = TransactionCategory.SALARY
E = TransactionCategory.ENTERTAINMENT
H = TransactionCategory.HEALTHCARE
SH = TransactionCategory.SHOPPING
TR = TransactionCategory.TRAVEL
CW = TransactionCategory.CASH_WITHDRAWAL
TF = TransactionCategory.TRANSFER
BF = TransactionCategory.BANK_FEES
UN = TransactionCategory.UNCATEGORIZED


def _cat(
    *,
    amount: float,
    description: str | None,
    counterparty_name: str | None = None,
    raw_category: str | None = None,
) -> TransactionCategory:
    """Thin wrapper so test lines stay under 100 characters."""
    return categorize(
        amount=amount,
        description=description,
        counterparty_name=counterparty_name,
        raw_category=raw_category,
    )


# ---------------------------------------------------------------------------
# Description keyword rules
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("amount", "description", "expected"),
    [
        (-23.40, "Card payment groceries", G),
        (-4.80, "Card payment cafe", D),
        (-48.75, "Card payment restaurant", D),
        (-890.00, "Rent May 2026 Apartment 4B", R),
        (-1234.56, "Miete Mai 2026 Whg 12", R),
        (-62.10, "Electricity April 2026, contract 99-1204", U),
        (-200.00, "Cash withdrawal", CW),
        (-150.00, "ATM WITHDRAWAL - MARLSTONE BANK HAUPTBAHNHOF", CW),
        (-100.00, "Geldautomat Hauptstrasse", CW),
        (-15.20, "Card authorization pharmacy", H),
        (-18.35, "Kartenzahlung Apotheke", H),
        (-12.99, "RECURRING PAYMENT - NIMBUS STREAMING", E),
        (-119.37, "Annual subscription, charged in USD", E),
        (-49.90, "Abo Mai 2026", E),
        (-168.74, "CARD PURCHASE - SKYBRIDGE AIRWAYS LISBON", TR),
        (-379.78, "Flugbuchung NYC", TR),
        (-2150.75, "Tagungspauschale", TR),
        (-4.90, "MONTHLY MAINTENANCE FEE", BF),
        (-5.90, "Kontofuehrungsgebuehr Mai", BF),
        (-77.00, "Membership June 2026", E),
    ],
)
def test_description_keyword_rule(
    amount: float, description: str, expected: TransactionCategory
) -> None:
    assert _cat(amount=amount, description=description) == expected


# ---------------------------------------------------------------------------
# Counterparty keyword rules
# ---------------------------------------------------------------------------
def test_eigenuebertrag_counterparty_is_transfer() -> None:
    result = _cat(amount=-500.00, description="Ruecklage Mai", counterparty_name="EIGENUEBERTRAG")
    assert result == TF


def test_transit_counterparty_is_transport() -> None:
    result = _cat(
        amount=-86.00, description="Monatskarte Mai 2026", counterparty_name="METRO TRANSIT"
    )
    assert result == T


# ---------------------------------------------------------------------------
# Amount-based rules
# ---------------------------------------------------------------------------
def test_large_positive_amount_is_salary() -> None:
    # Uses a neutral description so only the amount heuristic can fire.
    assert _cat(amount=2450.00, description="BRIGHTLINE CONSULTING PAYROLL") == S


def test_small_positive_amount_is_not_salary() -> None:
    result = _cat(amount=1.84, description="Interest credit Q2")
    assert result != S


def test_refund_keyword_positive_amount_is_shopping() -> None:
    assert _cat(amount=34.50, description="Refund for returned goods") == SH


def test_salary_heuristic_exact_threshold_is_salary() -> None:
    # amount == 1000.00 (the boundary) must yield SALARY (>= is inclusive).
    # Description has no salary keyword so only the heuristic can fire.
    assert _cat(amount=1000.00, description="Direct credit Jan") == S


def test_just_below_threshold_is_not_salary() -> None:
    # amount == 999.99 (one cent below threshold) must not yield SALARY.
    assert _cat(amount=999.99, description="Direct credit Jan") != S


def test_mid_range_below_threshold_is_not_salary() -> None:
    # amount == 750.00 is well below threshold and has no salary keyword.
    assert _cat(amount=750.00, description="Monthly savings plan") != S


# ---------------------------------------------------------------------------
# raw_category mapping
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw_category", "expected"),
    [
        ("Groceries", G),
        ("Restaurants", D),
        ("Income", S),
        ("Housing", R),
        ("Travel", TR),
        ("Shopping", SH),
        ("Entertainment", E),
        ("Cash", CW),
        ("Utilities", U),
        ("Health", H),
        ("Transportation", T),
        ("Transfer", TF),
        ("Fees", BF),
    ],
)
def test_raw_category_mapping(raw_category: str, expected: TransactionCategory) -> None:
    result = _cat(amount=-10.00, description="any text", raw_category=raw_category)
    assert result == expected


# ---------------------------------------------------------------------------
# Precedence and fallback
# ---------------------------------------------------------------------------
def test_no_signals_yields_uncategorized() -> None:
    assert _cat(amount=-9.99, description=None) == UN


def test_unrecognized_description_yields_uncategorized() -> None:
    assert _cat(amount=-9.99, description="Diverse miscellaneous charge xyz") == UN


def test_raw_category_takes_precedence_over_salary_heuristic() -> None:
    # raw_category is highest-priority group; "Income" should win regardless of amount
    result = _cat(amount=250.00, description="TRANSFER FROM CHECKING", raw_category="Income")
    assert result == S


def test_raw_category_takes_precedence_over_description() -> None:
    # raw_category="Groceries" wins even though description matches nothing else
    result = _cat(
        amount=-41.27, description="POS PURCHASE - GREENFIELD GROCERS", raw_category="Groceries"
    )
    assert result == G


def test_salary_heuristic_wins_over_description_for_large_inflow() -> None:
    # Large positive amount is SALARY even when description has no salary keyword.
    # "BRIGHTLINE CONSULTING GMBH" matches no keyword rule, so the heuristic is decisive.
    assert _cat(amount=2380.50, description="BRIGHTLINE CONSULTING GMBH") == S


def test_salary_heuristic_beats_keyword_rule_when_both_fire() -> None:
    # ADR-0005: group 2 (salary heuristic) takes precedence over group 3 (keywords).
    # "Eigenuebertrag" matches the TRANSFER keyword rule (group 3), but the
    # large positive amount fires the salary heuristic (group 2) first.
    assert _cat(amount=1500.00, description="Eigenuebertrag") == S


def test_categorize_is_pure() -> None:
    result1 = _cat(amount=-23.40, description="Card payment groceries")
    result2 = _cat(amount=-23.40, description="Card payment groceries")
    assert result1 == result2


# ---------------------------------------------------------------------------
# categorize_many
# ---------------------------------------------------------------------------
def test_categorize_many_empty_input() -> None:
    assert categorize_many([]) == []


def test_categorize_many_preserves_order() -> None:
    inputs = [
        dict(amount=2450.00, description="Salary", counterparty_name=None, raw_category=None),
        dict(amount=-9.99, description=None, counterparty_name=None, raw_category=None),
    ]
    results = categorize_many(inputs)
    assert results[0] == S
    assert results[1] == UN


def test_categorize_many_returns_one_per_input() -> None:
    inputs = [
        dict(amount=-23.40, description="groceries", counterparty_name=None, raw_category=None),
        dict(amount=-4.80, description="cafe", counterparty_name=None, raw_category=None),
    ]
    results = categorize_many(inputs)
    assert len(results) == 2
    assert results[0] == G
    assert results[1] == D


# ---------------------------------------------------------------------------
# Schema-drift guard
# ---------------------------------------------------------------------------
def test_categorize_returns_transaction_category() -> None:
    result = _cat(amount=-23.40, description="Card payment groceries")
    assert isinstance(result, TransactionCategory)


def test_all_non_uncategorized_categories_are_reachable() -> None:
    """Every non-UNCATEGORIZED category must be reachable by at least one rule.

    This test fails the moment a new enum value is added to TransactionCategory
    but no corresponding rule covers it -- a schema-drift guard.
    """
    from open_banking_pipeline.categorization import REACHABLE_CATEGORIES

    all_non_fallback = set(TransactionCategory) - {TransactionCategory.UNCATEGORIZED}
    assert all_non_fallback == REACHABLE_CATEGORIES, (
        f"Categories not covered by any rule: {all_non_fallback - REACHABLE_CATEGORIES}. "
        f"Add a rule or document why the category is intentionally unreachable."
    )
