from __future__ import annotations

import json
from typing import Any

import pytest

from jwt_util import knox_claims, sign_rs256

pytestmark = pytest.mark.gateway

MCP_URL = "/mcp/spark"


def _tool_call(client, token: str, name: str, arguments: dict[str, Any], rpc_id: int = 1):
    return client.post(
        MCP_URL,
        json={
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )


def _result_payload(response) -> tuple[dict[str, Any], dict[str, Any]]:
    body = response.json()["result"]
    return body, json.loads(body["content"][0]["text"])


def test_mcp_spark_requires_knox_jwt(client) -> None:
    response = client.post(
        MCP_URL,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"


def test_mcp_spark_lists_spark_tools(client) -> None:
    token = sign_rs256(knox_claims(sub="analyst"))
    response = client.post(
        MCP_URL,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    assert response.status_code == 200, response.text
    tools = {item["name"] for item in response.json()["result"]["tools"]}
    assert tools == {
        "spark_list_sessions",
        "spark_list_batches",
        "spark_get_batch",
        "spark_get_log",
        "spark_submit_batch",
    }


def test_mcp_spark_list_batches_forwards_subject(client) -> None:
    token = sign_rs256(knox_claims(sub="analyst"))
    response = _tool_call(client, token, "spark_list_batches", {}, rpc_id=2)
    assert response.status_code == 200, response.text
    body, payload = _result_payload(response)
    assert body["isError"] is False
    assert payload["kind"] == "batches"
    assert payload["knox_user"] == "analyst"
    assert payload["items"][0]["id"] == 0
    assert "Authorization" not in payload
    assert "token" not in json.dumps(payload)


def test_mcp_spark_submit_batch_accepts_hdfs_file(client) -> None:
    token = sign_rs256(knox_claims(sub="analyst"))
    response = _tool_call(
        client,
        token,
        "spark_submit_batch",
        {"file": "hdfs:///user/analyst/pi.py", "name": "pi"},
        rpc_id=3,
    )
    assert response.status_code == 200, response.text
    body, payload = _result_payload(response)
    assert body["isError"] is False
    assert payload["submitted"] is True
    assert payload["id"] == 1
    assert payload["knox_user"] == "analyst"
    assert payload["state"] == "starting"


def test_mcp_spark_submit_rejects_http_file(client) -> None:
    token = sign_rs256(knox_claims(sub="analyst"))
    response = _tool_call(
        client,
        token,
        "spark_submit_batch",
        {"file": "https://example.com/evil.py"},
        rpc_id=4,
    )
    assert response.status_code == 200, response.text
    body, payload = _result_payload(response)
    assert body["isError"] is True
    assert "HDFS" in payload["error"] or "object-store" in payload["error"]


def test_mcp_spark_submit_rejects_proxy_user(client) -> None:
    token = sign_rs256(knox_claims(sub="analyst"))
    response = _tool_call(
        client,
        token,
        "spark_submit_batch",
        {"file": "hdfs:///user/analyst/job.py", "proxyUser": "hive"},
        rpc_id=5,
    )
    assert response.status_code == 200, response.text
    body, payload = _result_payload(response)
    assert body["isError"] is True
    assert "proxyUser" in payload["error"]


def test_mcp_spark_submit_rejects_inline_code(client) -> None:
    token = sign_rs256(knox_claims(sub="analyst"))
    response = _tool_call(
        client,
        token,
        "spark_submit_batch",
        {"file": "hdfs:///user/analyst/job.py", "code": "print(1)"},
        rpc_id=6,
    )
    assert response.status_code == 200, response.text
    body, payload = _result_payload(response)
    assert body["isError"] is True
    assert "inline" in payload["error"] or "code" in payload["error"]
