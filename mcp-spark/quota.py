"""Talk to the operator admin service for per-sub quotas. Fail open if admin is down."""

from __future__ import annotations

import os
from typing import Any

import httpx

ADMIN_URL = os.environ.get("ADMIN_URL", "http://admin:8080").rstrip("/")
ADMIN_TOKEN = os.environ.get("ADMIN_INTERNAL_TOKEN", "lab-admin")
TIMEOUT = 2.0


class QuotaDenied(Exception):
    def __init__(self, decision: dict[str, Any]):
        super().__init__("gateway quota exceeded for this Knox user")
        self.decision = decision


def _headers() -> dict[str, str]:
    return {"X-Admin-Internal": ADMIN_TOKEN, "Content-Type": "application/json"}


def admit(*, sub: str | None, tool: str, request_id: str | None, token_id: str | None) -> dict[str, Any] | None:
    try:
        response = httpx.post(
            f"{ADMIN_URL}/internal/admit",
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
    try:
        httpx.post(
            f"{ADMIN_URL}/internal/event",
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
