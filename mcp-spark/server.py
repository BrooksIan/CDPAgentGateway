#!/usr/bin/env python3
"""Livy Spark 3 MCP adapter. List/get/log are reads; spark_submit_batch is a write as the Knox subject."""

from __future__ import annotations

import json
import os
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from livy import LivyError, build_submit_body, get_json, parse_batch_id, post_json
from quota import QuotaDenied, admit, record

PROTOCOL_VERSION = "2025-03-26"
SERVER_NAME = "mcp-spark"
SERVER_VERSION = "0.1.0"
PORT = int(os.environ.get("PORT", "8080"))

TOOLS = [
    {
        "name": "spark_list_sessions",
        "description": (
            "List Livy for Spark 3 interactive sessions for the Knox token subject. "
            "Read-only. Poll this instead of assuming sessions exist."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "spark_list_batches",
        "description": (
            "List Livy for Spark 3 batch jobs for the Knox token subject. "
            "Read-only. Use spark_get_batch to poll a single id."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "spark_get_batch",
        "description": "Get one Livy Spark 3 batch by numeric id (state, appId). Read-only.",
        "inputSchema": {
            "type": "object",
            "properties": {"batch_id": {"type": "integer", "minimum": 0}},
            "required": ["batch_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "spark_get_log",
        "description": (
            "Get truncated Livy Spark 3 batch logs (last lines, size-capped). "
            "Read-only. Do not expect a full executor dump."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"batch_id": {"type": "integer", "minimum": 0}},
            "required": ["batch_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "spark_submit_batch",
        "description": (
            "Submit a Livy Spark 3 batch as the Knox token subject (a write). "
            "Ranger authorizes that subject. "
            "file must be hdfs://, s3a://, abfs://, or o3fs:// (a jar or script Ranger allows). "
            "No inline code, no proxyUser. Poll with spark_get_batch."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "minLength": 1},
                "className": {"type": "string"},
                "name": {"type": "string"},
                "args": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
            },
            "required": ["file"],
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
                    "Spark tools over Knox Livy for Spark 3. "
                    "Send the caller's Knox JWT as Authorization. "
                    "spark_submit_batch is a write as that subject; file URIs only. "
                    "Do not call Hive or raw Knox."
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
        if name == "spark_list_sessions":
            result = get_json(
                "sessions",
                authorization=authorization,
                request_id=request_id,
                knox_user=knox_user,
            )
        elif name == "spark_list_batches":
            result = get_json(
                "batches",
                authorization=authorization,
                request_id=request_id,
                knox_user=knox_user,
            )
        elif name == "spark_get_batch":
            result = get_json(
                "batch",
                authorization=authorization,
                batch_id=parse_batch_id(arguments.get("batch_id")),
                request_id=request_id,
                knox_user=knox_user,
            )
        elif name == "spark_get_log":
            result = get_json(
                "log",
                authorization=authorization,
                batch_id=parse_batch_id(arguments.get("batch_id")),
                params={"size": 80},
                request_id=request_id,
                knox_user=knox_user,
            )
        elif name == "spark_submit_batch":
            result = post_json(
                "batches",
                authorization=authorization,
                payload=build_submit_body(arguments),
                request_id=request_id,
                knox_user=knox_user,
            )
        else:
            return _rpc_error(rpc_id, -32601, f"unknown tool: {name}")
    except QuotaDenied as exc:
        _log("quota_denied", tool=name, sub=knox_user, knox_id=token_id, request_id=request_id)
        return _tool_error(rpc_id, str(exc), {**exc.decision, "status": 429})
    except LivyError as exc:
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
