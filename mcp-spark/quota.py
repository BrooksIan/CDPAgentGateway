"""Talk to the operator admin service for per-sub quotas.

HTTP backend (Compose): fail open if admin is down.
SQLite backend (AMP): both apps share the project sqlite file.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

TIMEOUT = 2.0
_sqlite_cache: dict[str, Any] = {}


class QuotaDenied(Exception):
    def __init__(self, decision: dict[str, Any]):
        super().__init__("gateway quota exceeded for this Knox user")
        self.decision = decision


def _backend() -> str:
    return (os.environ.get("ADMIN_BACKEND") or "http").strip().lower()


def _admin_url() -> str:
    return (os.environ.get("ADMIN_URL") or "http://admin:8080").rstrip("/")


def _admin_token() -> str:
    return os.environ.get("ADMIN_INTERNAL_TOKEN") or "lab-admin"


def _headers() -> dict[str, str]:
    return {"X-Admin-Internal": _admin_token(), "Content-Type": "application/json"}


def _sqlite_db():
    from store import connect

    path = os.environ.get("ADMIN_DB") or "/data/gateway.sqlite"
    cached = _sqlite_cache.get(path)
    if cached is None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        cached = connect(path)
        _sqlite_cache[path] = cached
    return cached


def admit(*, sub: str | None, tool: str, request_id: str | None, token_id: str | None) -> dict[str, Any] | None:
    if _backend() == "sqlite":
        from store import admit as store_admit

        decision = store_admit(
            _sqlite_db(),
            sub=sub or "unknown",
            tool=tool,
            request_id=request_id,
            token_id=token_id,
        )
        if not decision.get("allowed", True):
            raise QuotaDenied(decision)
        return decision

    try:
        response = httpx.post(
            f"{_admin_url()}/internal/admit",
            headers=_headers(),
            json={"sub": sub or "unknown", "tool": tool, "request_id": request_id, "token_id": token_id},
            timeout=TIMEOUT,
        )
    except httpx.HTTPError:
        return None
    if response.status_code == 429:
        body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        raise QuotaDenied(body if isinstance(body, dict) else {})
    if response.status_code >= 400:
        return None
    return response.json() if response.content else {}


def record(
    *,
    sub: str | None,
    tool: str,
    ok: bool,
    request_id: str | None,
    token_id: str | None,
    status: int | None = None,
) -> None:
    if _backend() == "sqlite":
        from store import record_event

        record_event(
            _sqlite_db(),
            sub=sub or "unknown",
            tool=tool,
            kind="call",
            ok=ok,
            request_id=request_id,
            token_id=token_id,
            status=status,
        )
        return
    try:
        httpx.post(
            f"{_admin_url()}/internal/event",
            headers=_headers(),
            json={
                "sub": sub or "unknown",
                "tool": tool,
                "kind": "call",
                "ok": ok,
                "request_id": request_id,
                "token_id": token_id,
                "status": status,
            },
            timeout=TIMEOUT,
        )
    except httpx.HTTPError:
        return
