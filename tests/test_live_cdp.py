from __future__ import annotations

import json
import os

import httpx
import pytest

from agentgateway.env import gateway_url, load_env

pytestmark = pytest.mark.live

_ENV = load_env()


def _live_enabled() -> bool:
    return _ENV.get("GATEWAY_MODE") == "live" and bool(_ENV.get("KNOX_TOKEN"))


def _token() -> str:
    return _ENV["KNOX_TOKEN"]


def _base() -> str:
    return os.environ.get("GATEWAY_URL") or gateway_url(_ENV)


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {_token()}", "Accept": "application/json"}


def _mcp_payload(response) -> dict:
    body = response.json()["result"]
    assert body.get("isError") is False, response.text
    return json.loads(body["content"][0]["text"])


@pytest.mark.skipif(not _live_enabled(), reason="Set GATEWAY_MODE=live and KNOX_TOKEN to run")
def test_live_livy_spark3_sessions() -> None:
    with httpx.Client(base_url=_base(), timeout=30.0, verify=False) as client:
        response = client.get(
            "/cdp/livy_for_spark3/sessions",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert response.status_code != 401, response.text
        assert response.status_code < 500, response.text
        if response.status_code == 200:
            body = response.json()
            assert "sessions" in body
        listed = client.post(
            "/mcp/spark",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers=_auth(),
        )
        assert listed.status_code != 401, listed.text
        if listed.status_code == 200:
            names = {item["name"] for item in listed.json()["result"]["tools"]}
            assert "spark_list_batches" in names
            assert "spark_submit_batch" in names


@pytest.mark.skipif(not _live_enabled(), reason="Set GATEWAY_MODE=live and KNOX_TOKEN to run")
def test_live_hive_select_user_count_to_10() -> None:
    """P2-15: after spark_submit_batch of count_to_10.py, Hive reads {sub}.count_to_10."""
    with httpx.Client(base_url=_base(), timeout=60.0, verify=False) as client:
        listed = client.post(
            "/mcp/hive",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers=_auth(),
        )
        assert listed.status_code == 200, listed.text
        names = {item["name"] for item in listed.json()["result"]["tools"]}
        assert "hive_select" in names

        who = client.post(
            "/mcp/hive",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "hive_list_databases", "arguments": {}},
            },
            headers=_auth(),
        )
        assert who.status_code == 200, who.text
        knox_user = _mcp_payload(who)["knox_user"]
        assert knox_user

        described = client.post(
            "/mcp/hive",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "hive_describe_table",
                    "arguments": {"database": knox_user, "table": "count_to_10"},
                },
            },
            headers=_auth(),
        )
        assert described.status_code == 200, described.text
        describe = _mcp_payload(described)
        assert describe["kind"] == "describe"
        assert [col["name"] for col in describe["columns"]] == ["n"]

        selected = client.post(
            "/mcp/hive",
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "hive_select",
                    "arguments": {
                        "database": knox_user,
                        "table": "count_to_10",
                        "columns": ["n"],
                        "limit": 10,
                    },
                },
            },
            headers=_auth(),
        )
        assert selected.status_code == 200, selected.text
        payload = _mcp_payload(selected)
        assert payload["kind"] == "select"
        assert payload["database"] == knox_user
        assert payload["table"] == "count_to_10"
        assert payload["knox_user"] == knox_user
        assert [int(row["n"]) for row in payload["rows"]] == list(range(1, 11))
        dumped = json.dumps(payload)
        assert "Bearer" not in dumped
        assert "Authorization" not in dumped


@pytest.mark.skipif(not _live_enabled(), reason="Set GATEWAY_MODE=live and KNOX_TOKEN to run")
def test_live_cdp_hive_stays_unpublished() -> None:
    with httpx.Client(base_url=_base(), timeout=15.0, verify=False) as client:
        response = client.get(
            "/cdp/hive",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert response.status_code == 404, response.text
