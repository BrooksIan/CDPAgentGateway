#!/usr/bin/env python3
"""Hive MCP adapter. Read-only list/describe/select as the Knox token subject."""

from __future__ import annotations

import json
import os
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from hs2 import describe_table, list_databases, list_tables, select_rows
from quota import QuotaDenied, admit, record
from sql import HiveError

PROTOCOL_VERSION = "2025-03-26"
SERVER_NAME = "mcp-hive"
SERVER_VERSION = "0.1.0"
PORT = int(os.environ.get("PORT", "8080"))

TOOLS = [
    {
        "name": "hive_list_databases",
        "description": (
            "List Hive databases Ranger allows for the Knox token subject. "
            "Read-only. /cdp/hive stays unpublished."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "hive_list_tables",
        "description": "List tables in one Hive database for the Knox token subject. Read-only.",
        "inputSchema": {
            "type": "object",
            "properties": {"database": {"type": "string", "minLength": 1}},
            "required": ["database"],
            "additionalProperties": False,
        },
    },
    {
        "name": "hive_describe_table",
        "description": "Describe columns on one Hive table. Read-only. No DDL.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "database": {"type": "string", "minLength": 1},
                "table": {"type": "string", "minLength": 1},
            },
            "required": ["database", "table"],
            "additionalProperties": False,
        },
    },
    {
        "name": "hive_select",
        "description": (
            "SELECT named columns from one Hive table as the Knox token subject. "
            "columns is required (no SELECT *). limit max 50. No WHERE, no DDL/DML."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "database": {"type": "string", "minLength": 1},
                "table": {"type": "string", "minLength": 1},
                "columns": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "minItems": 1,
                    "maxItems": 16,
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["database", "table", "columns"],
            "additionalProperties": False,
        },
    },
]

_SECRET_LOG_KEYS = frozenset({"authorization", "bearer", "cookie", "password", "secret", "jwt", "token"})


def _log(event: str, **fields: Any) -> None:
    safe = {
        key: value
        for key, value in fields.items()
        if key.lower() not in _SECRET_LOG_KEYS and "authorization" not in key.lower()
    }
    payload = {"service": SERVER_NAME, "event": event, **safe}
    print(json.dumps(payload), flush=True)


async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": SERVER_NAME, "tools": [t["name"] for t in TOOLS]})


async def mcp_endpoint(request: Request) -> Response:
    if request.method == "GET":
        return JSONResponse({"error": "use POST JSON-RPC"}, status_code=405)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _rpc_error(None, -32700, "parse error")
    if not isinstance(body, dict):
        return _rpc_error(None, -32600, "invalid request")
    return _handle_rpc(body, request)


def _handle_rpc(body: dict[str, Any], request: Request) -> JSONResponse:
    rpc_id = body.get("id")
    method = body.get("method")
    if method == "notifications/initialized" or (method and str(method).startswith("notifications/")):
        return Response(status_code=202)
    if method == "initialize":
        return _rpc_result(
            rpc_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": (
                    "Read-only Hive tools over Knox HiveServer2. "
                    "Send the caller's Knox JWT as Authorization. "
                    "Ranger authorizes that subject. "
                    "Do not call /cdp/hive or raw Knox. No DDL/DML. No SELECT *."
                ),
            },
        )
    if method == "ping":
        return _rpc_result(rpc_id, {})
    if method == "tools/list":
        return _rpc_result(rpc_id, {"tools": TOOLS})
    if method == "tools/call":
        params = body.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        return _call_tool(rpc_id, name, arguments, request)
    return _rpc_error(rpc_id, -32601, f"method not found: {method}")


def _call_tool(rpc_id: Any, name: str, arguments: dict[str, Any], request: Request) -> JSONResponse:
    authorization = request.headers.get("authorization") or ""
    if not authorization.lower().startswith("bearer "):
        return _tool_error(rpc_id, "missing Knox bearer")
    knox_user = request.headers.get("x-knox-user")
    token_id = request.headers.get("x-knox-token-id")
    request_id = request.headers.get("x-request-id")
    try:
        admit(sub=knox_user, tool=name, request_id=request_id, token_id=token_id)
        if name == "hive_list_databases":
            result = list_databases(
                authorization=authorization,
                knox_user=knox_user,
                request_id=request_id,
            )
        elif name == "hive_list_tables":
            result = list_tables(
                arguments.get("database"),
                authorization=authorization,
                knox_user=knox_user,
                request_id=request_id,
            )
        elif name == "hive_describe_table":
            result = describe_table(
                arguments.get("database"),
                arguments.get("table"),
                authorization=authorization,
                knox_user=knox_user,
                request_id=request_id,
            )
        elif name == "hive_select":
            result = select_rows(
                database=arguments.get("database"),
                table=arguments.get("table"),
                columns=arguments.get("columns"),
                limit=arguments.get("limit"),
                authorization=authorization,
                knox_user=knox_user,
                request_id=request_id,
            )
        else:
            return _rpc_error(rpc_id, -32601, f"unknown tool: {name}")
    except QuotaDenied as exc:
        _log("quota_denied", tool=name, sub=knox_user, knox_id=token_id, request_id=request_id)
        return _tool_error(rpc_id, str(exc), {**exc.decision, "status": 429})
    except HiveError as exc:
        _log("tool_error", tool=name, sub=knox_user, knox_id=token_id, request_id=request_id, status=exc.status)
        record(
            sub=knox_user,
            tool=name,
            ok=False,
            request_id=request_id,
            token_id=token_id,
            status=exc.status,
        )
        return _tool_error(rpc_id, str(exc), {**exc.details, "status": exc.status})
    _log("tool_ok", tool=name, sub=knox_user, knox_id=token_id, request_id=request_id)
    record(sub=knox_user, tool=name, ok=True, request_id=request_id, token_id=token_id)
    return _rpc_result(
        rpc_id,
        {"content": [{"type": "text", "text": json.dumps(result, separators=(",", ":"))}], "isError": False},
    )


def _rpc_result(rpc_id: Any, result: dict[str, Any]) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": result})


def _rpc_error(rpc_id: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}})


def _tool_error(rpc_id: Any, message: str, extra: dict[str, Any] | None = None) -> JSONResponse:
    payload = {"error": message, **(extra or {})}
    return _rpc_result(
        rpc_id,
        {
            "content": [{"type": "text", "text": json.dumps(payload, separators=(",", ":"))}],
            "isError": True,
        },
    )


app = Starlette(
    routes=[
        Route("/health", health, methods=["GET"]),
        Route("/mcp", mcp_endpoint, methods=["GET", "POST"]),
        Route("/mcp/", mcp_endpoint, methods=["GET", "POST"]),
    ]
)


def main() -> None:
    import uvicorn

    _log("listen", port=PORT)
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
