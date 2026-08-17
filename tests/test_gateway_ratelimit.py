from __future__ import annotations

import os
import uuid

import pytest

from agentgateway.env import load_env
from jwt_util import knox_claims, mcp_headers, sign_rs256

pytestmark = pytest.mark.gateway

MCP_URL = "/mcp/spark"


def _list_tools(client, token: str, rpc_id: int = 1):
    return client.post(
        MCP_URL,
        json={"jsonrpc": "2.0", "id": rpc_id, "method": "tools/list"},
        headers=mcp_headers(token),
    )


def _authenticated_list(client, token: str):
    response = _list_tools(client, token)
    if response.status_code == 401:
        pytest.skip("APISIX is not using the local mock Knox PEM")
    return response


def test_mcp_rate_limit_header_on_authenticated_call(client) -> None:
    count = int(os.environ.get("MCP_RATE_COUNT") or load_env().get("MCP_RATE_COUNT") or "60")
    token = sign_rs256(knox_claims(sub="analyst"))
    response = _list_tools(client, token)
    if response.status_code == 401:
        live = load_env().get("KNOX_TOKEN")
        if not live:
            pytest.skip("APISIX is not using the local mock Knox PEM")
        response = _list_tools(client, live)
        if response.status_code == 401:
            pytest.skip("stored Knox token was rejected")
    assert response.status_code == 200, response.text
    assert response.headers.get("X-RateLimit-Limit") == str(count)


def test_mcp_rate_limit_is_per_knox_user(client) -> None:
    count = int(os.environ.get("MCP_RATE_COUNT") or load_env().get("MCP_RATE_COUNT") or "60")
    sub = f"rate-{uuid.uuid4().hex[:12]}"
    token = sign_rs256(knox_claims(sub=sub))
    first = _authenticated_list(client, token)
    assert first.status_code == 200, first.text

    last = first
    for i in range(count):
        last = _list_tools(client, token, rpc_id=i + 2)
        if last.status_code == 429:
            break
    assert last.status_code == 429, last.text
    assert "rate limit" in last.text.lower()

    livy = client.get(
        "/cdp/livy_for_spark3/sessions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert livy.status_code == 200, livy.text

    other = sign_rs256(knox_claims(sub=f"other-{uuid.uuid4().hex[:12]}"))
    ok = _list_tools(client, other, rpc_id=99)
    assert ok.status_code == 200, ok.text
