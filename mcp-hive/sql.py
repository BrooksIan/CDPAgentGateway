"""Build read-only Hive SQL from structured tool arguments. No free-form statements."""

from __future__ import annotations

import re
from typing import Any

MAX_ROWS = 50
MAX_COLS = 16
MAX_LIST_ITEMS = 100
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


class HiveError(Exception):
    def __init__(self, message: str, *, status: int = 400, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.details = details or {}


def ident(raw: Any, *, field: str) -> str:
    name = str(raw or "").strip().strip("`")
    if not IDENT.match(name):
        raise HiveError(f"{field} must be a Hive identifier", status=400)
    return name


def parse_limit(raw: Any, *, default: int = 20) -> int:
    if raw is None or raw == "":
        value = default
    elif isinstance(raw, bool):
        raise HiveError("limit must be an integer", status=400)
    elif isinstance(raw, int):
        value = raw
    elif isinstance(raw, str) and raw.isdigit():
        value = int(raw)
    else:
        raise HiveError("limit must be an integer", status=400)
    if value < 1 or value > MAX_ROWS:
        raise HiveError(f"limit must be between 1 and {MAX_ROWS}", status=400)
    return value


def column_list(raw: Any) -> list[str]:
    if raw is None:
        items: list[Any] = []
    elif isinstance(raw, str):
        items = [part.strip() for part in raw.split(",") if part.strip()]
    elif isinstance(raw, list):
        items = raw
    else:
        raise HiveError("columns must be a list of identifiers", status=400)
    if not items:
        raise HiveError("columns are required; SELECT * is not allowed", status=400)
    if len(items) > MAX_COLS:
        raise HiveError(f"columns are limited to {MAX_COLS}", status=400)
    return [ident(item, field="column") for item in items]


def show_databases_sql() -> str:
    return "SHOW DATABASES"


def show_tables_sql(database: Any) -> str:
    db = ident(database, field="database")
    return f"SHOW TABLES IN `{db}`"


def describe_sql(database: Any, table: Any) -> str:
    db = ident(database, field="database")
    tbl = ident(table, field="table")
    return f"DESCRIBE `{db}`.`{tbl}`"


def select_sql(database: Any, table: Any, columns: Any, limit: Any) -> tuple[str, int]:
    db = ident(database, field="database")
    tbl = ident(table, field="table")
    cols = column_list(columns)
    lim = parse_limit(limit)
    rendered = ", ".join(f"`{col}`" for col in cols)
    return f"SELECT {rendered} FROM `{db}`.`{tbl}` LIMIT {lim}", lim
