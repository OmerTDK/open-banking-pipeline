"""DuckDB landing store for canonical accounts and transactions.

Idempotency contract (ADR-0003): inserts are first-write-wins keyed on the
derived identifiers. Replaying an identical record is a no-op; the same
identifier arriving with *different* content is a ``LandingConflictError``,
never a silent overwrite — a changed upstream record must be a loud event,
not a guess.
"""

from collections.abc import Callable, Iterable
from pathlib import Path
from types import TracebackType
from typing import NamedTuple, Self

import duckdb

from open_banking_pipeline.canonical import CanonicalAccount, CanonicalTransaction

AMOUNT_PRECISION = 18
AMOUNT_SCALE = 4
AMOUNT_SQL_TYPE = f"DECIMAL({AMOUNT_PRECISION}, {AMOUNT_SCALE})"


class LandingColumn(NamedTuple):
    """One column of a landing table; the DDL and the data contract derive from it."""

    name: str
    sql_type: str
    is_nullable: bool
    is_primary_key: bool = False


ACCOUNTS_LANDING_COLUMNS = (
    LandingColumn("account_id", "VARCHAR", is_nullable=False, is_primary_key=True),
    LandingColumn("source_bank", "VARCHAR", is_nullable=False),
    LandingColumn("source_account_id", "VARCHAR", is_nullable=False),
    LandingColumn("display_name", "VARCHAR", is_nullable=False),
    LandingColumn("currency", "VARCHAR", is_nullable=False),
    LandingColumn("iban", "VARCHAR", is_nullable=True),
)
TRANSACTIONS_LANDING_COLUMNS = (
    LandingColumn("transaction_id", "VARCHAR", is_nullable=False, is_primary_key=True),
    LandingColumn("account_id", "VARCHAR", is_nullable=False),
    LandingColumn("source_bank", "VARCHAR", is_nullable=False),
    LandingColumn("source_account_id", "VARCHAR", is_nullable=False),
    LandingColumn("source_transaction_id", "VARCHAR", is_nullable=False),
    LandingColumn("status", "VARCHAR", is_nullable=False),
    LandingColumn("booking_date", "DATE", is_nullable=True),
    LandingColumn("value_date", "DATE", is_nullable=True),
    LandingColumn("amount", AMOUNT_SQL_TYPE, is_nullable=False),
    LandingColumn("currency", "VARCHAR", is_nullable=False),
    LandingColumn("counterparty_name", "VARCHAR", is_nullable=True),
    LandingColumn("counterparty_account", "VARCHAR", is_nullable=True),
    LandingColumn("description", "VARCHAR", is_nullable=True),
    LandingColumn("raw_category", "VARCHAR", is_nullable=True),
    LandingColumn("category", "VARCHAR", is_nullable=False),
)

ACCOUNT_COLUMNS = tuple(column.name for column in ACCOUNTS_LANDING_COLUMNS)
TRANSACTION_COLUMNS = tuple(column.name for column in TRANSACTIONS_LANDING_COLUMNS)


def _column_clause(column: LandingColumn) -> str:
    if column.is_primary_key:
        return f"{column.name} {column.sql_type} PRIMARY KEY"
    if column.is_nullable:
        return f"{column.name} {column.sql_type}"
    return f"{column.name} {column.sql_type} NOT NULL"


def _create_table_statement(table_name: str, columns: tuple[LandingColumn, ...]) -> str:
    clauses = ",\n    ".join(_column_clause(column) for column in columns)
    return f"CREATE TABLE IF NOT EXISTS {table_name} (\n    {clauses}\n)"


CREATE_ACCOUNTS_TABLE = _create_table_statement("accounts", ACCOUNTS_LANDING_COLUMNS)
CREATE_TRANSACTIONS_TABLE = _create_table_statement("transactions", TRANSACTIONS_LANDING_COLUMNS)


class LandingConflictError(Exception):
    """An identifier arrived again with different content; refusing to guess."""


class AmountScaleError(Exception):
    """An amount carries more decimal places than the landing schema stores losslessly."""


def _reject_out_of_scale_amount(transaction: CanonicalTransaction) -> None:
    decimal_places = -transaction.amount.as_tuple().exponent
    if decimal_places > AMOUNT_SCALE:
        raise AmountScaleError(
            f"transaction {transaction.transaction_id!r} amount {transaction.amount} has "
            f"{decimal_places} decimal places; DECIMAL({AMOUNT_PRECISION}, {AMOUNT_SCALE}) "
            f"would silently round it"
        )


class LandingStore:
    """Idempotent canonical landing store backed by a DuckDB database file."""

    def __init__(self, connection: duckdb.DuckDBPyConnection) -> None:
        self._connection = connection

    @classmethod
    def open(cls, database_path: Path) -> Self:
        """Open (creating directories and tables as needed) a store at ``database_path``."""
        database_path.parent.mkdir(parents=True, exist_ok=True)
        store = cls(duckdb.connect(str(database_path)))
        store.initialize_schema()
        return store

    def initialize_schema(self) -> None:
        """Create the accounts and transactions tables if they do not exist."""
        self._connection.execute(CREATE_ACCOUNTS_TABLE)
        self._connection.execute(CREATE_TRANSACTIONS_TABLE)

    def close(self) -> None:
        """Close the underlying DuckDB connection."""
        self._connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def insert_new_accounts(self, accounts: Iterable[CanonicalAccount]) -> int:
        """Insert unseen accounts; return how many were new."""
        return self._insert_atomically(
            [(account.account_id, account) for account in accounts],
            table_name="accounts",
            columns=ACCOUNT_COLUMNS,
            fetch_existing=self.get_account,
        )

    def insert_new_transactions(self, transactions: Iterable[CanonicalTransaction]) -> int:
        """Insert unseen transactions; return how many were new.

        Raises:
            AmountScaleError: An amount would be silently rounded by the schema.
            LandingConflictError: A transaction id arrived with different content.
        """
        identified_transactions = [
            (transaction.transaction_id, transaction) for transaction in transactions
        ]
        for _, transaction in identified_transactions:
            _reject_out_of_scale_amount(transaction)
        return self._insert_atomically(
            identified_transactions,
            table_name="transactions",
            columns=TRANSACTION_COLUMNS,
            fetch_existing=self.get_transaction,
        )

    def get_account(self, account_id: str) -> CanonicalAccount | None:
        """Return the landed account for ``account_id``, or ``None`` if absent."""
        row = self._fetch_row("accounts", ACCOUNT_COLUMNS, "account_id", account_id)
        if row is None:
            return None
        return CanonicalAccount.model_validate(dict(zip(ACCOUNT_COLUMNS, row, strict=True)))

    def get_transaction(self, transaction_id: str) -> CanonicalTransaction | None:
        """Return the landed transaction for ``transaction_id``, or ``None`` if absent."""
        row = self._fetch_row("transactions", TRANSACTION_COLUMNS, "transaction_id", transaction_id)
        if row is None:
            return None
        return CanonicalTransaction.model_validate(dict(zip(TRANSACTION_COLUMNS, row, strict=True)))

    def count_accounts(self) -> int:
        """Return how many accounts have landed."""
        return self._connection.execute("SELECT count(*) FROM accounts").fetchone()[0]

    def count_transactions(self) -> int:
        """Return how many transactions have landed."""
        return self._connection.execute("SELECT count(*) FROM transactions").fetchone()[0]

    def export_transactions_jsonl(self) -> bytes:
        """Serialize all transactions deterministically: id-ordered JSON lines."""
        column_list = ", ".join(TRANSACTION_COLUMNS)
        rows = self._connection.execute(
            f"SELECT {column_list} FROM transactions ORDER BY transaction_id"
        ).fetchall()
        lines = [
            CanonicalTransaction.model_validate(
                dict(zip(TRANSACTION_COLUMNS, row, strict=True))
            ).model_dump_json()
            for row in rows
        ]
        if not lines:
            return b""
        return ("\n".join(lines) + "\n").encode("utf-8")

    def _insert_atomically[Record: CanonicalAccount | CanonicalTransaction](
        self,
        identified_records: list[tuple[str, Record]],
        table_name: str,
        columns: tuple[str, ...],
        fetch_existing: Callable[[str], Record | None],
    ) -> int:
        inserted_count = 0
        self._connection.execute("BEGIN TRANSACTION")
        try:
            for record_id, record in identified_records:
                existing = fetch_existing(record_id)
                if existing is None:
                    self._insert_row(table_name, columns, record)
                    inserted_count += 1
                elif existing != record:
                    raise LandingConflictError(
                        f"{table_name.rstrip('s')} {record_id!r} already landed with "
                        f"different content; refusing first-write-wins overwrite"
                    )
            self._connection.execute("COMMIT")
        except Exception:
            self._connection.execute("ROLLBACK")
            raise
        return inserted_count

    def _insert_row(
        self,
        table_name: str,
        columns: tuple[str, ...],
        record: CanonicalAccount | CanonicalTransaction,
    ) -> None:
        placeholders = ", ".join("?" for _ in columns)
        values = [getattr(record, column) for column in columns]
        self._connection.execute(
            f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({placeholders})",
            values,
        )

    def _fetch_row(
        self,
        table_name: str,
        columns: tuple[str, ...],
        key_column: str,
        key_value: str,
    ) -> tuple | None:
        column_list = ", ".join(columns)
        return self._connection.execute(
            f"SELECT {column_list} FROM {table_name} WHERE {key_column} = ?",
            [key_value],
        ).fetchone()
