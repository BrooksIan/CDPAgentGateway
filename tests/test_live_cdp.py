from __future__ import annotations

import os

import httpx
import pytest

pytestmark = pytest.mark.live


def _live_enabled() -> bool:
    return os.environ.get("GATEWAY_MODE") == "live" and bool(os.environ.get("KNOX_TOKEN"))


@pytest.mark.skipif(not _live_enabled(), reason="Set GATEWAY_MODE=live and KNOX_TOKEN to run")
def test_live_livy_spark3_sessions() -> None:
    token = os.environ["KNOX_TOKEN"]
    base = os.environ.get("GATEWAY_URL", "http://127.0.0.1:9080")
    with httpx.Client(base_url=base, timeout=30.0, verify=False) as client:
        response = client.get(
            "/cdp/livy_for_spark3/sessions",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code != 401, response.text
        assert response.status_code < 500, response.text
        if response.status_code == 200:
            body = response.json()
            assert "sessions" in body
        listed = client.post(
            "/mcp/spark",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        assert listed.status_code != 401, listed.text
        if listed.status_code == 200:
            names = {item["name"] for item in listed.json()["result"]["tools"]}
            assert "spark_list_batches" in names
            assert "spark_submit_batch" in names
