from __future__ import annotations

from typing import Any

import httpx

from agentgateway.env import gateway_url
from agentgateway.knox import SPARK_MCP_PATH


def mcp_rpc(
    method: str,
    *,
    token: str,
    params: dict[str, Any] | None = None,
    rpc_id: int = 1,
    timeout: float = 30.0,
) -> httpx.Response:
    url = f"{gateway_url().rstrip('/')}{SPARK_MCP_PATH}"
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": rpc_id, "method": method}
    if params is not None:
        payload["params"] = params
    return httpx.post(
        url,
        json=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        timeout=timeout,
        follow_redirects=False,
    )
