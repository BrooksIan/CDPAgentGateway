"""Operator Impala probe. Forwards the caller's Knox JWT; never logs it.

Prefers an inventoried CDW coordinator (`IMPALA_HOST` / `httpPath=cliservice`).
Otherwise uses Knox `{KNOX_PROXY_PREFIX}/impala`. JDBC `auth=browser` is ignored.
"""

from __future__ import annotations

from typing import Any

from agentgateway.impyla_compat import connect_impyla
from agentgateway.knox import IMPALA_SERVICE, TOKEN_TOPOLOGY, impala_warehouse_host

REQUEST_TIMEOUT = 60.0
_SECRET_MESSAGE_MARKERS = ("bearer ", "authorization", "password", "token=")
MAX_HS2_MESSAGE = 400


class ImpalaError(Exception):
    pass


def _public_message(exc: BaseException, *, limit: int = MAX_HS2_MESSAGE) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    lowered = text.lower()
    if any(marker in lowered for marker in _SECRET_MESSAGE_MARKERS):
        return exc.__class__.__name__
    return text[:limit]


def impala_http_path(env: dict[str, str]) -> str:
    """CDW `cliservice`, or Knox token-topology `/impala` (not cdp-proxy-api)."""
    if impala_warehouse_host(env):
        return (env.get("IMPALA_HTTP_PATH") or "cliservice").strip().lstrip("/")
    prefix = (env.get("KNOX_PROXY_PREFIX") or "").strip()
    if not prefix:
        raise ImpalaError("KNOX_PROXY_PREFIX is empty; run gateway knox <livy-url>")
    if TOKEN_TOPOLOGY not in prefix:
        raise ImpalaError(
            f"Impala JWT probe needs {TOKEN_TOPOLOGY} in KNOX_PROXY_PREFIX, got {prefix!r}"
        )
    return f"{prefix.strip('/')}/{IMPALA_SERVICE}"


def impala_target(env: dict[str, str]) -> str:
    warehouse = impala_warehouse_host(env)
    path = impala_http_path(env)
    if warehouse:
        scheme = (env.get("IMPALA_SCHEME") or "https").lower()
        port = env.get("IMPALA_PORT") or ("443" if scheme == "https" else "80")
        return f"{scheme}://{warehouse}:{port}/{path}"
    return path


def impala_connect_kwargs(env: dict[str, str], token: str) -> dict[str, Any]:
    if not token:
        raise ImpalaError("missing Knox bearer")
    warehouse = impala_warehouse_host(env)
    if warehouse:
        scheme = (env.get("IMPALA_SCHEME") or "https").lower()
        verify = (env.get("IMPALA_TLS_VERIFY") or env.get("UPSTREAM_TLS_VERIFY") or "false").lower() in {
            "true",
            "1",
            "yes",
        }
        return {
            "host": warehouse,
            "port": int(env.get("IMPALA_PORT") or ("443" if scheme == "https" else "80")),
            "auth_mechanism": "JWT",
            "jwt": token,
            "use_ssl": scheme == "https",
            "use_http_transport": True,
            "http_path": impala_http_path(env),
            "timeout": REQUEST_TIMEOUT,
            "verify_cert": verify,
        }
    host = (env.get("UPSTREAM_HOST") or "").strip()
    if not host or host == "mock-cdp":
        raise ImpalaError("Impala probe needs a live Knox host or IMPALA_HOST; run gateway jdbc add")
    scheme = (env.get("UPSTREAM_SCHEME") or "https").lower()
    verify = (env.get("UPSTREAM_TLS_VERIFY") or "false").lower() in {"true", "1", "yes"}
    return {
        "host": host,
        "port": int(env.get("UPSTREAM_PORT") or ("443" if scheme == "https" else "80")),
        "auth_mechanism": "JWT",
        "jwt": token,
        "use_ssl": scheme == "https",
        "use_http_transport": True,
        "http_path": impala_http_path(env),
        "timeout": REQUEST_TIMEOUT,
        "verify_cert": verify,
    }


def show_databases(env: dict[str, str], token: str) -> list[str]:
    try:
        from impala.dbapi import connect
    except ImportError as exc:
        raise ImpalaError("Impala client missing; pip install 'impyla>=0.19'") from exc
    try:
        conn = connect_impyla(connect, impala_connect_kwargs(env, token))
    except Exception as exc:  # noqa: BLE001 — HS2/thrift errors become probe errors
        raise ImpalaError(_public_message(exc)) from exc
    try:
        cursor = conn.cursor()
        try:
            cursor.execute("SHOW DATABASES")
            rows = cursor.fetchall()
        finally:
            try:
                cursor.close()
            except Exception:  # noqa: BLE001
                pass
    except ImpalaError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ImpalaError(_public_message(exc)) from exc
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001 — close after a failed OpenSession is noisy
            pass
    names: list[str] = []
    for row in rows:
        name = row[0] if isinstance(row, (list, tuple)) else row
        if name is not None:
            names.append(str(name))
    return names
