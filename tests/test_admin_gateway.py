from __future__ import annotations

import json
import os

import httpx
import pytest

from jwt_util import knox_claims, sign_rs256

pytestmark = pytest.mark.gateway

ADMIN_URL = os.environ.get("ADMIN_URL", "http://127.0.0.1:9090").rstrip("/")
MCP_URL = "/mcp/spark"


def test_admin_ui_is_on_operator_port_not_apisix(client) -> None:
    local = httpx.get(f"{ADMIN_URL}/health", timeout=5.0)
    assert local.status_code == 200, local.text
    assert local.json()["service"] == "admin"
    page = httpx.get(f"{ADMIN_URL}/", timeout=5.0)
    assert page.status_code == 200
    assert "Operator" in page.text
    assert "knox.id" in page.text
    via_gateway = client.get("/admin")
    assert via_gateway.status_code == 404


def test_admin_admit_enforces_zero_submit_quota() -> None:
    token = os.environ.get("ADMIN_INTERNAL_TOKEN", "lab-admin")
    headers = {"X-Admin-Internal": token, "Content-Type": "application/json"}
    quota = httpx.put(
        f"{ADMIN_URL}/api/quotas/quota-user",
        json={"daily_calls": None, "daily_submits": 0},
        timeout=5.0,
    )
    assert quota.status_code == 200, quota.text
    denied = httpx.post(
        f"{ADMIN_URL}/internal/admit",
        headers=headers,
        json={"sub": "quota-user", "tool": "spark_submit_batch", "request_id": "t-quota"},
        timeout=5.0,
    )
    assert denied.status_code == 429, denied.text
    body = denied.json()
    assert body["allowed"] is False
    assert body["reason"] == "submit_quota"
    httpx.delete(f"{ADMIN_URL}/api/quotas/quota-user", timeout=5.0)


def test_submit_quota_blocks_mcp_for_that_subject(client) -> None:
    quota = httpx.put(
        f"{ADMIN_URL}/api/quotas/quota-user",
        json={"daily_calls": None, "daily_submits": 0},
        timeout=5.0,
    )
    assert quota.status_code == 200, quota.text
    token = sign_rs256(knox_claims(sub="quota-user"))
    response = client.post(
        MCP_URL,
        json={
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {
                "name": "spark_submit_batch",
                "arguments": {"file": "hdfs:///user/quota-user/job.py"},
            },
        },
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    if response.status_code == 401:
        httpx.delete(f"{ADMIN_URL}/api/quotas/quota-user", timeout=5.0)
        pytest.skip("APISIX is not using the local mock Knox PEM")
    assert response.status_code == 200, response.text
    body = response.json()["result"]
    assert body["isError"] is True
    payload = json.loads(body["content"][0]["text"])
    assert payload.get("status") == 429
    assert "quota" in payload["error"].lower()
    httpx.delete(f"{ADMIN_URL}/api/quotas/quota-user", timeout=5.0)


def test_audit_join_by_request_id() -> None:
    token = os.environ.get("ADMIN_INTERNAL_TOKEN", "lab-admin")
    missing = httpx.get(f"{ADMIN_URL}/api/audit", timeout=5.0)
    assert missing.status_code == 400
    unknown = httpx.get(f"{ADMIN_URL}/api/audit", params={"request_id": "no-such-id"}, timeout=5.0)
    assert unknown.status_code == 404
    recorded = httpx.post(
        f"{ADMIN_URL}/internal/event",
        headers={"X-Admin-Internal": token, "Content-Type": "application/json"},
        json={
            "sub": "analyst",
            "tool": "spark_list_batches",
            "kind": "call",
            "ok": True,
            "request_id": "p2-04-gateway",
            "token_id": "test-token-id",
        },
        timeout=5.0,
    )
    assert recorded.status_code == 200, recorded.text
    joined = httpx.get(f"{ADMIN_URL}/api/audit", params={"request_id": "p2-04-gateway"}, timeout=5.0)
    assert joined.status_code == 200, joined.text
    audit = joined.json()["audit"]
    assert audit["tool"] == "spark_list_batches"
    assert audit["sub"] == "analyst"
    assert audit["knox.id"] == "test-token-id"
    assert audit["request_id"] == "p2-04-gateway"
    dumped = json.dumps(joined.json())
    assert "Bearer" not in dumped
    assert "Authorization" not in dumped


def test_mcp_audit_join_uses_request_id(client) -> None:
    token = sign_rs256(knox_claims(sub="analyst"))
    response = client.post(
        MCP_URL,
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "spark_list_batches", "arguments": {}},
        },
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    if response.status_code == 401:
        pytest.skip("APISIX is not using the local mock Knox PEM")
    assert response.status_code == 200, response.text
    request_id = response.headers.get("X-Request-Id")
    assert request_id
    joined = httpx.get(f"{ADMIN_URL}/api/audit", params={"request_id": request_id}, timeout=5.0)
    assert joined.status_code == 200, joined.text
    audit = joined.json()["audit"]
    assert audit["tool"] == "spark_list_batches"
    assert audit["sub"] == "analyst"
    assert audit["knox.id"] == "test-token-id"
    assert audit["request_id"] == request_id
    dumped = json.dumps(joined.json())
    assert "Bearer" not in dumped
    assert token not in dumped
