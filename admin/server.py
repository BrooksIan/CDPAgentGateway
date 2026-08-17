#!/usr/bin/env python3
"""Operator admin UI: Knox-user usage and Spark tool quotas. Not an agent route."""

from __future__ import annotations

import json
import os
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from store import (
    DEFAULT_SUB,
    admit,
    connect,
    delete_quota,
    get_audit,
    list_events,
    list_quotas,
    list_users,
    overview,
    parse_day,
    record_event,
    set_quota,
    utc_day,
)

PORT = int(os.environ.get("PORT", "8080"))
DB_PATH = os.environ.get("ADMIN_DB", "/data/gateway.sqlite")
INTERNAL_TOKEN = os.environ.get("ADMIN_INTERNAL_TOKEN", "lab-admin")
STATIC = Path(__file__).resolve().parent / "static"
APISIX_HEALTH_URL = os.environ.get("APISIX_HEALTH_URL", "http://apisix:9080/health")
MCP_SPARK_HEALTH_URL = os.environ.get("MCP_SPARK_HEALTH_URL", "http://mcp-spark:8080/health")

_db = None


def db():
    global _db
    if _db is None:
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        _db = connect(DB_PATH)
    return _db


def _query_day(request: Request) -> str:
    try:
        return parse_day(request.query_params.get("day")) or utc_day()
    except ValueError as exc:
        raise ValueError(str(exc)) from exc


def _probe(url: str) -> str:
    from urllib.error import URLError
    from urllib.request import urlopen

    try:
        with urlopen(url, timeout=1.5) as response:  # noqa: S310 — operator health URLs from env
            return "ok" if 200 <= response.status < 300 else "down"
    except (URLError, TimeoutError, OSError, ValueError):
        return "down"


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name) or str(default)
    try:
        number = int(raw)
    except ValueError:
        return default
    return number if number >= 1 else default


def _json_error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _require_internal(request: Request) -> JSONResponse | None:
    token = request.headers.get("x-admin-internal") or ""
    if not INTERNAL_TOKEN or token != INTERNAL_TOKEN:
        return _json_error(401, "internal token required")
    return None


async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "admin", "ui": True})


async def index(_request: Request) -> FileResponse:
    return FileResponse(STATIC / "index.html")


async def api_overview(request: Request) -> JSONResponse:
    try:
        day = _query_day(request)
    except ValueError as exc:
        return _json_error(400, str(exc))
    return JSONResponse(overview(db(), day=day))


async def api_users(request: Request) -> JSONResponse:
    try:
        day = _query_day(request)
    except ValueError as exc:
        return _json_error(400, str(exc))
    return JSONResponse({"users": list_users(db(), day=day), "quotas": list_quotas(db()), "day": day})


async def api_events(request: Request) -> JSONResponse:
    try:
        day = _query_day(request)
        limit = int(request.query_params.get("limit") or 50)
    except ValueError as exc:
        return _json_error(400, str(exc))
    try:
        events = list_events(
            db(),
            limit=limit,
            sub=request.query_params.get("sub"),
            tool=request.query_params.get("tool"),
            result=request.query_params.get("result"),
            day=day,
        )
    except ValueError as exc:
        return _json_error(400, str(exc))
    return JSONResponse({"events": events, "day": day})


async def api_status(_request: Request) -> JSONResponse:
    mcp = _probe(MCP_SPARK_HEALTH_URL)
    apisix = _probe(APISIX_HEALTH_URL)
    return JSONResponse(
        {
            "service": "admin",
            "quotas": "enforcing",
            "fail_open": True,
            "fail_open_note": "mcp-spark allows Spark calls if this service is down",
            "burst": {
                "count": _positive_int("MCP_RATE_COUNT", 60),
                "window": _positive_int("MCP_RATE_WINDOW", 60),
                "route": "/mcp/spark",
                "in_sqlite": False,
            },
            "mode": os.environ.get("GATEWAY_MODE") or "local",
            "upstream_host": os.environ.get("UPSTREAM_HOST") or "mock-cdp",
            "health": {"admin": "ok", "mcp_spark": mcp, "apisix": apisix},
        }
    )


async def api_audit(request: Request) -> JSONResponse:
    request_id = (request.query_params.get("request_id") or "").strip()
    if not request_id:
        return _json_error(400, "request_id is required")
    record = get_audit(db(), request_id)
    if record is None:
        return _json_error(404, "audit not found")
    return JSONResponse({"audit": record})


async def api_put_quota(request: Request) -> JSONResponse:
    sub = request.path_params.get("sub") or DEFAULT_SUB
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _json_error(400, "invalid json")
    if not isinstance(body, dict):
        return _json_error(400, "invalid json")
    try:
        quota = set_quota(
            db(),
            sub,
            daily_calls=body.get("daily_calls"),
            daily_submits=body.get("daily_submits"),
        )
    except ValueError as exc:
        return _json_error(400, str(exc))
    return JSONResponse({"quota": quota})


async def api_delete_quota(request: Request) -> JSONResponse:
    sub = request.path_params.get("sub") or ""
    try:
        deleted = delete_quota(db(), sub)
    except ValueError as exc:
        return _json_error(400, str(exc))
    if not deleted:
        return _json_error(404, "quota not found")
    return JSONResponse({"deleted": sub})


async def internal_admit(request: Request) -> Response:
    denied = _require_internal(request)
    if denied:
        return denied
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _json_error(400, "invalid json")
    if not isinstance(body, dict):
        return _json_error(400, "invalid json")
    decision = admit(
        db(),
        sub=str(body.get("sub") or ""),
        tool=str(body.get("tool") or ""),
        request_id=body.get("request_id"),
        token_id=body.get("token_id"),
    )
    status = 200 if decision["allowed"] else 429
    return JSONResponse(decision, status_code=status)


async def internal_event(request: Request) -> Response:
    denied = _require_internal(request)
    if denied:
        return denied
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _json_error(400, "invalid json")
    if not isinstance(body, dict):
        return _json_error(400, "invalid json")
    event = record_event(
        db(),
        sub=str(body.get("sub") or ""),
        tool=str(body.get("tool") or ""),
        kind=str(body.get("kind") or "call"),
        ok=body.get("ok"),
        request_id=body.get("request_id"),
        token_id=body.get("token_id"),
        status=body.get("status"),
    )
    return JSONResponse({"event": event})


app = Starlette(
    routes=[
        Route("/health", health, methods=["GET"]),
        Route("/", index, methods=["GET"]),
        Route("/api/overview", api_overview, methods=["GET"]),
        Route("/api/users", api_users, methods=["GET"]),
        Route("/api/events", api_events, methods=["GET"]),
        Route("/api/status", api_status, methods=["GET"]),
        Route("/api/audit", api_audit, methods=["GET"]),
        Route("/api/quotas/{sub:path}", api_put_quota, methods=["PUT"]),
        Route("/api/quotas/{sub:path}", api_delete_quota, methods=["DELETE"]),
        Route("/internal/admit", internal_admit, methods=["POST"]),
        Route("/internal/event", internal_event, methods=["POST"]),
        Mount("/static", StaticFiles(directory=str(STATIC)), name="static"),
    ]
)


def main() -> None:
    import uvicorn

    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    connect(DB_PATH)
    print(json.dumps({"service": "admin", "event": "listen", "port": PORT}), flush=True)
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
