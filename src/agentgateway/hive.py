"""Operator Hive probe over Knox HTTP. Forwards the caller's Knox JWT; never logs it."""

from __future__ import annotations

from typing import Any

from agentgateway.knox import HIVE_SERVICE, TOKEN_TOPOLOGY

REQUEST_TIMEOUT = 60.0


class HiveError(Exception):
    pass


def hive_http_path(env: dict[str, str]) -> str:
    """JWT Hive hop is the token topology, not cdp-proxy-api."""
    prefix = (env.get("KNOX_PROXY_PREFIX") or "").strip()
    if not prefix:
        raise HiveError("KNOX_PROXY_PREFIX is empty; run gateway knox <livy-url>")
    if TOKEN_TOPOLOGY not in prefix:
        raise HiveError(
            f"Hive JWT probe needs {TOKEN_TOPOLOGY} in KNOX_PROXY_PREFIX, got {prefix!r}"
        )
    return f"{prefix.strip('/')}/{HIVE_SERVICE}"


def hive_connect_kwargs(env: dict[str, str], token: str) -> dict[str, Any]:
    host = (env.get("UPSTREAM_HOST") or "").strip()
    if not host or host == "mock-cdp":
        raise HiveError("Hive probe needs a live Knox host; run gateway knox <url>")
    if not token:
        raise HiveError("missing Knox bearer")
    scheme = (env.get("UPSTREAM_SCHEME") or "https").lower()
    verify = (env.get("UPSTREAM_TLS_VERIFY") or "false").lower() in {"true", "1", "yes"}
    return {
        "host": host,
        "port": int(env.get("UPSTREAM_PORT") or ("443" if scheme == "https" else "80")),
        "auth_mechanism": "JWT",
        "jwt": token,
        "use_ssl": scheme == "https",
        "use_http_transport": True,
        "http_path": hive_http_path(env),
        "timeout": REQUEST_TIMEOUT,
        "verify_cert": verify,
    }


def show_databases(env: dict[str, str], token: str) -> list[str]:
    try:
        from impala.dbapi import connect
    except ImportError as exc:
        raise HiveError("Hive client missing; pip install 'impyla>=0.19'") from exc
    conn = connect(**hive_connect_kwargs(env, token))
    try:
        cursor = conn.cursor()
        try:
            cursor.execute("SHOW DATABASES")
            rows = cursor.fetchall()
        finally:
            cursor.close()
    finally:
        conn.close()
    names: list[str] = []
    for row in rows:
        name = row[0] if isinstance(row, (list, tuple)) else row
        if name is not None:
            names.append(str(name))
    return names
