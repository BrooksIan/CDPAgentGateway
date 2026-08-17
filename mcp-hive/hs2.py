"""HiveServer2 client. Forwards the caller's Knox JWT on the token topology. Never logs it."""

from __future__ import annotations

import os
from typing import Any

from sql import (
    HiveError,
    MAX_LIST_ITEMS,
    MAX_ROWS,
    column_list,
    describe_sql,
    ident,
    select_sql,
    show_databases_sql,
    show_tables_sql,
)

HIVE_SERVICE = "hive"
TOKEN_TOPOLOGY = "cdp-proxy-token"
REQUEST_TIMEOUT = 60.0

MOCK_DATABASES = ["analytics", "default"]
MOCK_TABLES = {
    "analytics": ["events"],
    "default": ["dual"],
}
MOCK_COLUMNS = {
    ("default", "dual"): [{"name": "dummy_col", "type": "string", "comment": ""}],
    ("analytics", "events"): [
        {"name": "event_id", "type": "string", "comment": ""},
        {"name": "ts", "type": "string", "comment": ""},
    ],
}


def is_mock() -> bool:
    host = (os.environ.get("UPSTREAM_HOST") or "mock-cdp").strip()
    return host in {"", "mock-cdp", "127.0.0.1", "localhost"}


def hive_http_path() -> str:
    prefix = (os.environ.get("KNOX_PROXY_PREFIX") or "").strip()
    if not prefix:
        raise HiveError("KNOX_PROXY_PREFIX is empty", status=500)
    if TOKEN_TOPOLOGY not in prefix:
        raise HiveError(
            f"Hive MCP needs {TOKEN_TOPOLOGY} in KNOX_PROXY_PREFIX, got {prefix!r}",
            status=500,
        )
    return f"{prefix.strip('/')}/{HIVE_SERVICE}"


def _bearer(authorization: str) -> str:
    if not authorization.lower().startswith("bearer "):
        raise HiveError("missing Knox bearer", status=401)
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HiveError("missing Knox bearer", status=401)
    return token


def _connect_kwargs(token: str) -> dict[str, Any]:
    host = (os.environ.get("UPSTREAM_HOST") or "").strip()
    if not host or host == "mock-cdp":
        raise HiveError("Hive MCP needs a live Knox host", status=500)
    scheme = (os.environ.get("UPSTREAM_SCHEME") or "https").lower()
    verify = (os.environ.get("UPSTREAM_TLS_VERIFY") or "false").lower() in {"true", "1", "yes"}
    return {
        "host": host,
        "port": int(os.environ.get("UPSTREAM_PORT") or ("443" if scheme == "https" else "80")),
        "auth_mechanism": "JWT",
        "jwt": token,
        "use_ssl": scheme == "https",
        "use_http_transport": True,
        "http_path": hive_http_path(),
        "timeout": REQUEST_TIMEOUT,
        "verify_cert": verify,
    }


def _fetch(sql: str, *, authorization: str) -> tuple[list[str], list[tuple[Any, ...]]]:
    try:
        from impala.dbapi import connect
    except ImportError as exc:
        raise HiveError("Hive client missing; install impyla", status=500) from exc
    conn = connect(**_connect_kwargs(_bearer(authorization)))
    try:
        cursor = conn.cursor()
        try:
            cursor.execute(sql)
            columns = [str(item[0]) for item in (cursor.description or [])]
            rows = cursor.fetchmany(MAX_ROWS)
        finally:
            cursor.close()
    finally:
        conn.close()
    return columns, list(rows or [])


def list_databases(*, authorization: str, knox_user: str | None, request_id: str | None) -> dict[str, Any]:
    if is_mock():
        names = list(MOCK_DATABASES)
    else:
        _columns, rows = _fetch(show_databases_sql(), authorization=authorization)
        names = [str(row[0]) for row in rows if row and row[0] is not None]
    sliced = names[:MAX_LIST_ITEMS]
    return {
        "kind": "databases",
        "returned": len(sliced),
        "truncated": len(names) > MAX_LIST_ITEMS,
        "items": sliced,
        "knox_user": knox_user,
        "request_id": request_id,
    }


def list_tables(
    database: Any,
    *,
    authorization: str,
    knox_user: str | None,
    request_id: str | None,
) -> dict[str, Any]:
    db = ident(database, field="database")
    if is_mock():
        if db not in MOCK_TABLES:
            raise HiveError(f"unknown database {db!r}", status=404)
        names = list(MOCK_TABLES[db])
    else:
        _columns, rows = _fetch(show_tables_sql(db), authorization=authorization)
        names = [str(row[0]) for row in rows if row and row[0] is not None]
    sliced = names[:MAX_LIST_ITEMS]
    return {
        "kind": "tables",
        "database": db,
        "returned": len(sliced),
        "truncated": len(names) > MAX_LIST_ITEMS,
        "items": sliced,
        "knox_user": knox_user,
        "request_id": request_id,
    }


def describe_table(
    database: Any,
    table: Any,
    *,
    authorization: str,
    knox_user: str | None,
    request_id: str | None,
) -> dict[str, Any]:
    db = ident(database, field="database")
    tbl = ident(table, field="table")
    if is_mock():
        columns = MOCK_COLUMNS.get((db, tbl))
        if columns is None:
            raise HiveError(f"unknown table {db}.{tbl}", status=404)
    else:
        col_names, rows = _fetch(describe_sql(db, tbl), authorization=authorization)
        columns = []
        for row in rows[:MAX_LIST_ITEMS]:
            columns.append(
                {
                    "name": str(row[0]) if row else "",
                    "type": str(row[1]) if row and len(row) > 1 else "",
                    "comment": str(row[2]) if row and len(row) > 2 and row[2] is not None else "",
                }
            )
        _ = col_names
    return {
        "kind": "describe",
        "database": db,
        "table": tbl,
        "columns": columns,
        "knox_user": knox_user,
        "request_id": request_id,
    }


def select_rows(
    *,
    database: Any,
    table: Any,
    columns: Any,
    limit: Any,
    authorization: str,
    knox_user: str | None,
    request_id: str | None,
) -> dict[str, Any]:
    sql, lim = select_sql(database, table, columns, limit)
    db = ident(database, field="database")
    tbl = ident(table, field="table")
    col_names = column_list(columns)
    if is_mock():
        schema = MOCK_COLUMNS.get((db, tbl))
        if schema is None:
            raise HiveError(f"unknown table {db}.{tbl}", status=404)
        allowed = {item["name"] for item in schema}
        missing = [name for name in col_names if name not in allowed]
        if missing:
            raise HiveError(f"unknown column {missing[0]!r}", status=400)
        row = {name: "ok" if name == "dummy_col" else "1" for name in col_names}
        rows = [row]
    else:
        fetched_cols, fetched_rows = _fetch(sql, authorization=authorization)
        rows = []
        for raw in fetched_rows[:lim]:
            item = {}
            names = [str(name) for name in (fetched_cols or col_names)]
            for idx, name in enumerate(names):
                item[name] = None if idx >= len(raw) else _cell(raw[idx])
            rows.append(item)
        col_names = [str(name) for name in (fetched_cols or col_names)]
    return {
        "kind": "select",
        "database": db,
        "table": tbl,
        "columns": col_names,
        "returned": len(rows),
        "limit": lim,
        "truncated": False,
        "rows": rows,
        "knox_user": knox_user,
        "request_id": request_id,
    }


def _cell(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        text = str(value)
        return text[:512]
    return str(value)[:512]
