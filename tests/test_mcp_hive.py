from __future__ import annotations

import json
from typing import Any

import pytest

from jwt_util import knox_claims, sign_rs256

pytestmark = pytest.mark.gateway

MCP_URL = "/mcp/hive"


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


def test_mcp_hive_requires_knox_jwt(client) -> None:
    response = client.post(
        MCP_URL,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"


def test_mcp_hive_lists_read_only_tools(client) -> None:
    token = sign_rs256(knox_claims(sub="analyst"))
    response = client.post(
        MCP_URL,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    assert response.status_code == 200, response.text
    tools = {item["name"] for item in response.json()["result"]["tools"]}
    assert tools == {
        "hive_list_databases",
        "hive_list_tables",
        "hive_describe_table",
        "hive_select",
    }


def test_mcp_hive_list_databases_forwards_subject(client) -> None:
    token = sign_rs256(knox_claims(sub="analyst"))
    response = _tool_call(client, token, "hive_list_databases", {}, rpc_id=2)
    assert response.status_code == 200, response.text
    body, payload = _result_payload(response)
    assert body["isError"] is False
    assert payload["kind"] == "databases"
    assert payload["knox_user"] == "analyst"
    assert "default" in payload["items"]
    dumped = json.dumps(payload)
    assert "Authorization" not in dumped
    assert "Bearer" not in dumped


def test_mcp_hive_select_named_columns(client) -> None:
    token = sign_rs256(knox_claims(sub="analyst"))
    response = _tool_call(
        client,
        token,
        "hive_select",
        {"database": "default", "table": "dual", "columns": ["dummy_col"], "limit": 5},
        rpc_id=3,
    )
    assert response.status_code == 200, response.text
    body, payload = _result_payload(response)
    assert body["isError"] is False
    assert payload["kind"] == "select"
    assert payload["rows"][0]["dummy_col"] == "ok"
    assert payload["limit"] == 5


def test_mcp_hive_select_rejects_star_and_bad_ident(client) -> None:
    token = sign_rs256(knox_claims(sub="analyst"))
    missing_cols = _tool_call(
        client,
        token,
        "hive_select",
        {"database": "default", "table": "dual", "columns": []},
        rpc_id=4,
    )
    body, payload = _result_payload(missing_cols)
    assert body["isError"] is True
    injected = _tool_call(
        client,
        token,
        "hive_select",
        {"database": "default;drop", "table": "dual", "columns": ["dummy_col"]},
        rpc_id=5,
    )
    body, payload = _result_payload(injected)
    assert body["isError"] is True
    assert "identifier" in payload["error"]


def test_cdp_hive_stays_unpublished(client) -> None:
    token = sign_rs256(knox_claims(sub="analyst"))
    response = client.get("/cdp/hive", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 404
