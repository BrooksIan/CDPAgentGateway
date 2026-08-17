from __future__ import annotations

from pathlib import Path

import pytest

from agentgateway.amp_apisix import build_amp_apisix_env, write_amp_apisix_config
from agentgateway.env import render_apisix_yaml

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "conf" / "apisix.yaml.tpl").read_text()


def test_amp_apisix_env_points_at_sibling_mcp_apps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CDSW_DOMAIN", "ml.example.com")
    monkeypatch.setenv(
        "KNOX_PROXY_URL",
        "https://knox.example.com/gateway/cdp-proxy-token/livy_for_spark3/",
    )
    values = build_amp_apisix_env()
    assert values["GATEWAY_PUBLIC_URL"] == "https://agent-gateway.ml.example.com"
    assert values["MCP_SPARK_UPSTREAM_SCHEME"] == "https"
    assert values["MCP_SPARK_UPSTREAM_HOST"] == "mcp-spark.ml.example.com"
    assert values["MCP_SPARK_UPSTREAM_PORT"] == "443"
    assert values["MCP_SPARK_PASS_HOST"] == "rewrite"


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


def test_write_amp_apisix_config_requires_pem(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CDSW_DOMAIN", "ml.example.com")
    monkeypatch.setenv(
        "KNOX_PROXY_URL",
        "https://knox.example.com/gateway/cdp-proxy-token/livy_for_spark3/",
    )
    with pytest.raises(FileNotFoundError, match="knox-public.pem"):
        write_amp_apisix_config(tmp_path)
