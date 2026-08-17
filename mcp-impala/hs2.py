"""Impala HS2 client. Forwards the caller's Knox JWT. Never logs it.

Uses inventoried CDW `IMPALA_HOST` (`httpPath=cliservice`) when set; otherwise Knox
`{KNOX_PROXY_PREFIX}/impala`. JDBC `auth=browser` is not implemented — agents send the JWT.
"""

from __future__ import annotations

import os
from typing import Any

from sql import (
    ImpalaError,
    MAX_LIST_ITEMS,
    MAX_ROWS,
    column_list,
    describe_sql,
    ident,
    select_sql,
    show_databases_sql,
    show_tables_sql,
)

IMPALA_SERVICE = "impala"
TOKEN_TOPOLOGY = "cdp-proxy-token"
REQUEST_TIMEOUT = 60.0
_SECRET_MESSAGE_MARKERS = ("bearer ", "authorization", "password", "token=")
MAX_HS2_MESSAGE = 400

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


def warehouse_host() -> str:
    host = (os.environ.get("IMPALA_HOST") or "").strip()
    if host in {"", "mock-cdp", "127.0.0.1", "localhost"}:
        return ""
    return host


def is_mock() -> bool:
    if warehouse_host():
        return False
    host = (os.environ.get("UPSTREAM_HOST") or "mock-cdp").strip()
    return host in {"", "mock-cdp", "127.0.0.1", "localhost"}


def impala_http_path() -> str:
    if warehouse_host():
        return (os.environ.get("IMPALA_HTTP_PATH") or "cliservice").strip().lstrip("/")
    prefix = (os.environ.get("KNOX_PROXY_PREFIX") or "").strip()
    if not prefix:
        raise ImpalaError("KNOX_PROXY_PREFIX is empty", status=500)
    if TOKEN_TOPOLOGY not in prefix:
        raise ImpalaError(
            f"Impala MCP needs {TOKEN_TOPOLOGY} in KNOX_PROXY_PREFIX, got {prefix!r}",
            status=500,
        )
    return f"{prefix.strip('/')}/{IMPALA_SERVICE}"


def _bearer(authorization: str) -> str:
    if not authorization.lower().startswith("bearer "):
        raise ImpalaError("missing Knox bearer", status=401)
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise ImpalaError("missing Knox bearer", status=401)
    return token


def _connect_kwargs(token: str) -> dict[str, Any]:
    cdw = warehouse_host()
    if cdw:
        scheme = (os.environ.get("IMPALA_SCHEME") or "https").lower()
        verify = (os.environ.get("IMPALA_TLS_VERIFY") or os.environ.get("UPSTREAM_TLS_VERIFY") or "false").lower() in {
            "true",
            "1",
            "yes",
        }
        return {
            "host": cdw,
            "port": int(os.environ.get("IMPALA_PORT") or ("443" if scheme == "https" else "80")),
            "auth_mechanism": "JWT",
            "jwt": token,
            "use_ssl": scheme == "https",
            "use_http_transport": True,
            "http_path": impala_http_path(),
            "timeout": REQUEST_TIMEOUT,
            "verify_cert": verify,
        }
    host = (os.environ.get("UPSTREAM_HOST") or "").strip()
    if not host or host == "mock-cdp":
        raise ImpalaError("Impala MCP needs a live Knox host or IMPALA_HOST", status=500)
    scheme = (os.environ.get("UPSTREAM_SCHEME") or "https").lower()
    verify = (os.environ.get("UPSTREAM_TLS_VERIFY") or "false").lower() in {"true", "1", "yes"}
    return {
        "host": host,
        "port": int(os.environ.get("UPSTREAM_PORT") or ("443" if scheme == "https" else "80")),
        "auth_mechanism": "JWT",
        "jwt": token,
        "use_ssl": scheme == "https",
        "use_http_transport": True,
        "http_path": impala_http_path(),
        "timeout": REQUEST_TIMEOUT,
        "verify_cert": verify,
    }


def _public_hs2_message(exc: BaseException, *, limit: int = MAX_HS2_MESSAGE) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    lowered = text.lower()
    if any(marker in lowered for marker in _SECRET_MESSAGE_MARKERS):
        return exc.__class__.__name__
    return text[:limit]


def impala_error_from_hs2(exc: BaseException) -> ImpalaError:
    raw = str(exc).strip() or exc.__class__.__name__
    lowered = raw.lower()
    status = 502
    if (
        "table not found" in lowered
        or "could not resolve table" in lowered
        or "table does not exist" in lowered
        or "could not resolve column" in lowered
    ):
        status = 404
    elif "database" in lowered and ("not found" in lowered or "does not exist" in lowered):
        status = 404
    elif "http code 401" in lowered or " 401:" in lowered:
        status = 401
    elif (
        "access denied" in lowered
        or "permission denied" in lowered
        or "authorizationexception" in lowered
        or "does not have privileges" in lowered
        or "http code 403" in lowered
        or " 403:" in lowered
    ):
        status = 403
    elif "has no attribute 'close'" in lowered:
        return ImpalaError("Impala HS2 rejected the request", status=502)
    return ImpalaError(_public_hs2_message(exc), status=status)


def _fetch(sql: str, *, authorization: str) -> tuple[list[str], list[tuple[Any, ...]]]:
    try:
        from impala.dbapi import connect
    except ImportError as exc:
        raise ImpalaError("Impala client missing; install impyla", status=500) from exc
    try:
        conn = connect(**_connect_kwargs(_bearer(authorization)))
        try:
            cursor = conn.cursor()
            try:
                cursor.execute(sql)
                columns = [str(item[0]) for item in (cursor.description or [])]
                rows = cursor.fetchmany(MAX_ROWS)
            finally:
                try:
                    cursor.close()
                except Exception:  # noqa: BLE001
                    pass
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    except ImpalaError:
        raise
    except Exception as exc:  # noqa: BLE001 — HS2/thrift errors become tool errors
        raise impala_error_from_hs2(exc) from exc
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
            raise ImpalaError(f"unknown database {db!r}", status=404)
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


def _describe_columns(rows: list[tuple[Any, ...]]) -> list[dict[str, str]]:
    columns: list[dict[str, str]] = []
    for row in rows[:MAX_LIST_ITEMS]:
        name = str(row[0]).strip() if row and row[0] is not None else ""
        if not name or name.startswith("#"):
            continue
        columns.append(
            {
                "name": name,
                "type": str(row[1]) if row and len(row) > 1 and row[1] is not None else "",
                "comment": str(row[2]) if row and len(row) > 2 and row[2] is not None else "",
            }
        )
    return columns


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
            raise ImpalaError(f"unknown table {db}.{tbl}", status=404)
    else:
        _col_names, rows = _fetch(describe_sql(db, tbl), authorization=authorization)
        columns = _describe_columns(rows)
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
            raise ImpalaError(f"unknown table {db}.{tbl}", status=404)
        allowed = {item["name"] for item in schema}
        missing = [name for name in col_names if name not in allowed]
        if missing:
            raise ImpalaError(f"unknown column {missing[0]!r}", status=400)
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
