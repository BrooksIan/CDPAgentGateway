from __future__ import annotations

from pathlib import Path

import pytest

from agentgateway.amp_apisix import build_amp_apisix_env
from agentgateway.env import render_apisix_yaml

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "conf" / "apisix.yaml.tpl").read_text()


def test_amp_apisix_env_points_at_sibling_mcp_apps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CDSW_DOMAIN", "ml.example.com")
    monkeypatch.setenv(
        "KNOX_PROXY_URL",
        "https://knox.example.com/gateway/cdp-proxy-token/livy_for_spark3/",
    )
    monkeypatch.delenv("AGENT_CALLER_KEY", raising=False)
    values = build_amp_apisix_env()
    assert values["GATEWAY_PUBLIC_URL"] == "https://agent-gateway.ml.example.com"
    assert values["MCP_SPARK_UPSTREAM_SCHEME"] == "https"
    assert values["MCP_SPARK_UPSTREAM_HOST"] == "mcp-spark.ml.example.com"
    assert values["MCP_SPARK_UPSTREAM_PORT"] == "443"
    assert values["MCP_SPARK_PASS_HOST"] == "rewrite"
    assert values.get("AGENT_CALLER_KEY") == ""


def test_amp_apisix_render_uses_https_upstreams(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CDSW_DOMAIN", "ml.example.com")
    monkeypatch.setenv(
        "KNOX_PROXY_URL",
        "https://knox.example.com/gateway/cdp-proxy-token/livy_for_spark3/",
    )
    values = build_amp_apisix_env()
    rendered = render_apisix_yaml(TEMPLATE, values)
    assert 'scheme: https' in rendered
    assert 'upstream_host: mcp-spark.ml.example.com' in rendered
    assert '"mcp-spark.ml.example.com:443": 1' in rendered
    assert "uri: /cdp/webhdfs*" in rendered


def test_write_amp_apisix_config_fetches_missing_pem(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CDSW_DOMAIN", "ml.example.com")
    monkeypatch.setenv(
        "KNOX_PROXY_URL",
        "https://knox.example.com/gateway/cdp-proxy-token/livy_for_spark3/",
    )
    pem = tmp_path / "conf" / "generated" / "knox-public.pem"

    def fake_fetch(*, knox_proxy_url, jwks_url, out, insecure):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("PEM")
        return out

    monkeypatch.setattr("agentgateway.amp_apisix.fetch_pinned_knox_pubkey", fake_fetch)
    from agentgateway.amp_apisix import ensure_amp_knox_pem

    assert ensure_amp_knox_pem(tmp_path) == pem
    assert pem.read_text() == "PEM"


def test_python_edge_health_is_public(monkeypatch: pytest.MonkeyPatch) -> None:
    from starlette.testclient import TestClient

    from agentgateway.amp_apisix import build_python_edge_app
    from agentgateway.keys import generate_test_keys

    monkeypatch.setenv("CDSW_DOMAIN", "ml.example.com")
    monkeypatch.setenv(
        "KNOX_PROXY_URL",
        "https://knox.example.com/gateway/cdp-proxy-token/livy_for_spark3/",
    )
    generate_test_keys()
    pem = ROOT / "conf" / "keys" / "public.pem"
    monkeypatch.setenv("KNOX_PUBLIC_KEY_FILE", str(pem))
    monkeypatch.setenv("ADMIN_BACKEND", "sqlite")
    client = TestClient(build_python_edge_app())
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["engine"] == "python"
    assert body["mcp"] == "inprocess"
    denied = client.post("/mcp/spark", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert denied.status_code == 401


def test_python_edge_tools_list_in_process(monkeypatch: pytest.MonkeyPatch) -> None:
    from starlette.testclient import TestClient

    from agentgateway.amp_apisix import build_python_edge_app
    from agentgateway.keys import generate_test_keys
    from agentgateway.token import knox_claims, sign_rs256

    monkeypatch.setenv("CDSW_DOMAIN", "ml.example.com")
    monkeypatch.setenv(
        "KNOX_PROXY_URL",
        "https://knox.example.com/gateway/cdp-proxy-token/livy_for_spark3/",
    )
    generate_test_keys()
    pem = ROOT / "conf" / "keys" / "public.pem"
    monkeypatch.setenv("KNOX_PUBLIC_KEY_FILE", str(pem))
    monkeypatch.setenv("AGENT_CALLER_KEY", "")
    monkeypatch.setenv("ADMIN_BACKEND", "sqlite")
    monkeypatch.setenv("ADMIN_DB", str(ROOT / "data" / "gateway.sqlite"))
    client = TestClient(build_python_edge_app())
    token = sign_rs256(knox_claims(sub="analyst"))
    response = client.post(
        "/mcp/spark",
        headers={"Authorization": f"Bearer {token}"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert response.status_code == 200, response.text
    names = [tool["name"] for tool in response.json()["result"]["tools"]]
    assert "spark_list_batches" in names


def test_python_edge_list_batches_loads_admin_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import sys

    from starlette.testclient import TestClient

    from agentgateway.amp_apisix import build_python_edge_app
    from agentgateway.keys import generate_test_keys
    from agentgateway.token import knox_claims, sign_rs256

    monkeypatch.setenv("CDSW_DOMAIN", "ml.example.com")
    monkeypatch.setenv(
        "KNOX_PROXY_URL",
        "https://knox.example.com/gateway/cdp-proxy-token/livy_for_spark3/",
    )
    generate_test_keys()
    pem = ROOT / "conf" / "keys" / "public.pem"
    monkeypatch.setenv("KNOX_PUBLIC_KEY_FILE", str(pem))
    monkeypatch.setenv("AGENT_CALLER_KEY", "")
    monkeypatch.setenv("ADMIN_BACKEND", "sqlite")
    monkeypatch.setenv("ADMIN_DB", str(tmp_path / "gateway.sqlite"))
    app = build_python_edge_app()
    spark = sys.modules["amp_edge_spark"]

    def fake_get_json(*_args, **kwargs):
        return {"kind": "batches", "items": [], "knox_user": kwargs.get("knox_user"), "returned": 0}

    monkeypatch.setattr(spark, "get_json", fake_get_json)
    client = TestClient(app)
    token = sign_rs256(knox_claims(sub="analyst"))
    response = client.post(
        "/mcp/spark",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "spark_list_batches", "arguments": {}},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result"]["isError"] is False


def test_startup_error_app_includes_detail() -> None:
    from starlette.testclient import TestClient

    from agentgateway.amp import startup_error_app

    app = startup_error_app("agent-gateway", FileNotFoundError("missing pem"))
    body = TestClient(app).get("/health").json()
    assert body["reason"] == "startup_failed"
    assert body["error"] == "FileNotFoundError"
    assert "missing pem" in body["detail"]
