from __future__ import annotations

from typing import Any

import httpx

from agentgateway.env import agent_headers, gateway_url
from agentgateway.knox import HIVE_MCP_PATH, SPARK_MCP_PATH

ADAPTERS = {
    "spark": SPARK_MCP_PATH,
    "hive": HIVE_MCP_PATH,
}


def mcp_path(adapter: str = "spark") -> str:
    key = (adapter or "spark").strip().lower()
    if key not in ADAPTERS:
        raise ValueError(f"unknown MCP adapter {adapter!r}; use spark or hive")
    return ADAPTERS[key]


def mcp_rpc(
    method: str,
    *,
    token: str,
    params: dict[str, Any] | None = None,
    rpc_id: int = 1,
    timeout: float = 30.0,
    adapter: str = "spark",
) -> httpx.Response:
    url = f"{gateway_url().rstrip('/')}{mcp_path(adapter)}"
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": rpc_id, "method": method}
    if params is not None:
        payload["params"] = params
    return httpx.post(
        url,
        json=payload,
        headers={
            **agent_headers(token, path=mcp_path(adapter)),
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        timeout=timeout,
        follow_redirects=False,
    )
