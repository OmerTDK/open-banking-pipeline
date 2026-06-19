"""Deterministic rule-based transaction categorizer (ADR-0005).

Rules operate on three signals extracted from the canonical transaction:
  1. ``raw_category`` — the bank's own label (normalized, matched exactly).
  2. ``amount`` — sign and magnitude for inflow/salary heuristics.
  3. ``description`` / ``counterparty_name`` — keyword search (case-insensitive).

Precedence (first-match, top-wins):
  Rule group 1 — raw_category mapping (bank label normalisation)
  Rule group 2 — large-inflow salary heuristic (amount >= SALARY_INFLOW_THRESHOLD)
  Rule group 3 — description / counterparty keyword rules

Within each group rules are evaluated in the order they appear in RULES.
The first matching rule determines the category; evaluation stops.
If no rule matches, the fallback is UNCATEGORIZED.

Design decisions are in ADR-0005.
"""

from decimal import Decimal
from typing import Any

from open_banking_pipeline.canonical import CanonicalTransaction, TransactionCategory

# A payroll credit below this threshold is plausible (part-time, bonus partial),
# but values above this are almost certainly a salary or equivalent inflow.
# The threshold sits between the smallest fixture salary (2 380.50) and the
# largest non-salary inflow (300.00 savings transfer).
SALARY_INFLOW_THRESHOLD = Decimal(1000)

# Raw category labels shipped by the mock banks (marlstone).
# Mapping is case-insensitive (normalised to lower before lookup).
_RAW_CATEGORY_MAP: dict[str, TransactionCategory] = {
    "groceries": TransactionCategory.GROCERIES,
    "restaurants": TransactionCategory.DINING,
    "income": TransactionCategory.SALARY,
    "housing": TransactionCategory.RENT,
    "travel": TransactionCategory.TRAVEL,
    "shopping": TransactionCategory.SHOPPING,
    "entertainment": TransactionCategory.ENTERTAINMENT,
    "cash": TransactionCategory.CASH_WITHDRAWAL,
    "utilities": TransactionCategory.UTILITIES,
    "health": TransactionCategory.HEALTHCARE,
    "transportation": TransactionCategory.TRANSPORT,
    "transfer": TransactionCategory.TRANSFER,
    "fees": TransactionCategory.BANK_FEES,
}

# Keyword rules for description + counterparty_name (case-insensitive).
# Format: (keyword_substring, TransactionCategory).
# Evaluated in order; first match wins within this group.
# Current count: 62 entries — update docs/adr/0005 and README when adding rules.
_KEYWORD_RULES: tuple[tuple[str, TransactionCategory], ...] = (
    # Cash / ATM — tested before generic "bank" or counterparty keywords
    ("cash withdrawal", TransactionCategory.CASH_WITHDRAWAL),
    ("atm withdrawal", TransactionCategory.CASH_WITHDRAWAL),
    ("geldautomat", TransactionCategory.CASH_WITHDRAWAL),
    # Intra-account transfers and SEPA self-transfers
    ("eigenuebertrag", TransactionCategory.TRANSFER),
    ("transfer", TransactionCategory.TRANSFER),
    # Groceries
    ("groceries", TransactionCategory.GROCERIES),
    ("lebensmittel", TransactionCategory.GROCERIES),
    ("supermarket", TransactionCategory.GROCERIES),
    # Dining
    ("cafe", TransactionCategory.DINING),
    ("coffee", TransactionCategory.DINING),
    ("restaurant", TransactionCategory.DINING),
    ("trattoria", TransactionCategory.DINING),
    ("bistro", TransactionCategory.DINING),
    # Rent / housing
    ("rent", TransactionCategory.RENT),
    ("miete", TransactionCategory.RENT),
    ("hausverwaltung", TransactionCategory.RENT),
    # Utilities
    ("electricity", TransactionCategory.UTILITIES),
    ("strom", TransactionCategory.UTILITIES),
    ("energy", TransactionCategory.UTILITIES),
    ("gas", TransactionCategory.UTILITIES),
    ("water", TransactionCategory.UTILITIES),
    ("internet", TransactionCategory.UTILITIES),
    ("telefon", TransactionCategory.UTILITIES),
    # Healthcare
    ("pharmacy", TransactionCategory.HEALTHCARE),
    ("apotheke", TransactionCategory.HEALTHCARE),
    ("arzt", TransactionCategory.HEALTHCARE),
    ("doctor", TransactionCategory.HEALTHCARE),
    ("hospital", TransactionCategory.HEALTHCARE),
    # Entertainment / subscriptions
    ("streaming", TransactionCategory.ENTERTAINMENT),
    ("subscription", TransactionCategory.ENTERTAINMENT),
    ("abo", TransactionCategory.ENTERTAINMENT),
    ("membership", TransactionCategory.ENTERTAINMENT),
    ("mitgliedsbeitrag", TransactionCategory.ENTERTAINMENT),
    ("cinema", TransactionCategory.ENTERTAINMENT),
    ("kino", TransactionCategory.ENTERTAINMENT),
    ("netflix", TransactionCategory.ENTERTAINMENT),
    ("spotify", TransactionCategory.ENTERTAINMENT),
    # Travel / flights / hotels
    ("airways", TransactionCategory.TRAVEL),
    ("airlines", TransactionCategory.TRAVEL),
    ("flugbuchung", TransactionCategory.TRAVEL),
    ("hotel", TransactionCategory.TRAVEL),
    ("tagungspauschale", TransactionCategory.TRAVEL),
    ("booking.com", TransactionCategory.TRAVEL),
    # Transport (local)
    ("transit", TransactionCategory.TRANSPORT),
    ("monatskarte", TransactionCategory.TRANSPORT),
    ("bahn", TransactionCategory.TRANSPORT),
    ("bus", TransactionCategory.TRANSPORT),
    ("taxi", TransactionCategory.TRANSPORT),
    ("uber", TransactionCategory.TRANSPORT),
    # Shopping (generic retail — after more-specific categories)
    ("refund", TransactionCategory.SHOPPING),
    ("electronics", TransactionCategory.SHOPPING),
    ("books", TransactionCategory.SHOPPING),
    ("buecher", TransactionCategory.SHOPPING),
    ("shop", TransactionCategory.SHOPPING),
    # Bank fees
    ("maintenance fee", TransactionCategory.BANK_FEES),
    ("kontofuehrungsgebuehr", TransactionCategory.BANK_FEES),
    ("gebuehr", TransactionCategory.BANK_FEES),
    ("annual fee", TransactionCategory.BANK_FEES),
    # Salary / income keywords (lower-precedence than amount threshold)
    ("salary", TransactionCategory.SALARY),
    ("gehalt", TransactionCategory.SALARY),
    ("payroll", TransactionCategory.SALARY),
    ("lohn", TransactionCategory.SALARY),
)

# The set of categories reachable by at least one rule (excluding UNCATEGORIZED).
# Used by the schema-drift test to verify every enum value is covered.
REACHABLE_CATEGORIES: frozenset[TransactionCategory] = frozenset(
    {category for _, category in _KEYWORD_RULES} | set(_RAW_CATEGORY_MAP.values())
)


def _apply_raw_category(raw_category: str | None) -> TransactionCategory | None:
    """Map a bank-supplied label to a canonical category, or return None."""
    if raw_category is None:
        return None
    return _RAW_CATEGORY_MAP.get(raw_category.strip().lower())


def _apply_salary_heuristic(amount: float | Decimal) -> TransactionCategory | None:
    """Return SALARY for large positive inflows; None otherwise."""
    if Decimal(str(amount)) >= SALARY_INFLOW_THRESHOLD:
        return TransactionCategory.SALARY
    return None


def _apply_keyword_rules(
    description: str | None,
    counterparty_name: str | None,
) -> TransactionCategory | None:
    """Return the first keyword match across description and counterparty_name."""
    search_text = " ".join(
        part.lower() for part in [description, counterparty_name] if part is not None
    )
    if not search_text:
        return None
    for keyword, category in _KEYWORD_RULES:
        if keyword in search_text:
            return category
    return None


def categorize(
    *,
    amount: float | Decimal,
    description: str | None,
    counterparty_name: str | None,
    raw_category: str | None,
) -> TransactionCategory:
    """Classify one transaction into a canonical spend category.

    Precedence (first-match, top-wins):
      1. raw_category mapping
      2. large-inflow salary heuristic
      3. keyword rules over description + counterparty_name

    Returns:
        The matched TransactionCategory, or UNCATEGORIZED when no rule fires.
    """
    raw_result = _apply_raw_category(raw_category)
    if raw_result is not None:
        return raw_result

    salary_result = _apply_salary_heuristic(amount)
    if salary_result is not None:
        return salary_result

    keyword_result = _apply_keyword_rules(description, counterparty_name)
    if keyword_result is not None:
        return keyword_result

    return TransactionCategory.UNCATEGORIZED


def apply_category(transaction: CanonicalTransaction) -> CanonicalTransaction:
    """Return a copy of ``transaction`` with its category field populated.

    Always re-runs the categorization rules regardless of the current value of
    ``category`` — the categorizer is the authoritative source of that field.

    Args:
        transaction: A canonical transaction (typically with category=UNCATEGORIZED).

    Returns:
        A new CanonicalTransaction identical to the input except for ``category``.
    """
    category = categorize(
        amount=transaction.amount,
        description=transaction.description,
        counterparty_name=transaction.counterparty_name,
        raw_category=transaction.raw_category,
    )
    return transaction.model_copy(update={"category": category})


def categorize_many(
    transactions: list[dict[str, Any]],
) -> list[TransactionCategory]:
    """Apply ``categorize`` to every item in a batch of transaction dicts.

    Each dict must supply ``amount``, ``description``, ``counterparty_name``,
    and ``raw_category`` keys (matching the kwargs of ``categorize``).

    Returns:
        One TransactionCategory per input item, in the same order.
    """
    return [
        categorize(
            amount=tx["amount"],
            description=tx["description"],
            counterparty_name=tx["counterparty_name"],
            raw_category=tx["raw_category"],
        )
        for tx in transactions
    ]
