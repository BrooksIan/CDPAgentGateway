from __future__ import annotations

from pathlib import Path

import pytest

from agentgateway.env import render_apisix_yaml

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "conf" / "apisix.yaml.tpl").read_text()

BASE = {
    "UPSTREAM_SCHEME": "http",
    "UPSTREAM_HOST": "mock-cdp",
    "UPSTREAM_PORT": "8080",
    "UPSTREAM_TLS_VERIFY": "false",
    "KNOX_ISSUER": "KNOXSSO",
    "KNOX_EXPECTED_ALG": "RS256",
    "KNOX_CLOCK_SKEW": "60",
    "KNOX_PROXY_PREFIX": "/gateway/cdp-proxy-token",
}


def _route_block(rendered: str, route_id: str) -> str:
    marker = f"- id: {route_id}"
    start = rendered.index(marker)
    rest = rendered[start + len(marker) :]
    nxt = rest.find("\n  - id:")
    return rest if nxt < 0 else rest[:nxt]


def test_mcp_route_has_limit_count_keyed_by_knox_user() -> None:
    rendered = render_apisix_yaml(TEMPLATE, BASE)
    mcp = _route_block(rendered, "mcp-spark-http")
    livy = _route_block(rendered, "spark-livy")
    assert "limit-count:" in mcp
    assert "key: knox_user" in mcp
    assert "count: 60" in mcp
    assert "rejected_code: 429" in mcp
    assert "limit-count:" not in livy


def test_mcp_rate_count_is_templated() -> None:
    rendered = render_apisix_yaml(TEMPLATE, {**BASE, "MCP_RATE_COUNT": "12", "MCP_RATE_WINDOW": "30"})
    assert "count: 12" in rendered
    assert "time_window: 30" in rendered


def test_mcp_hive_route_has_limit_count() -> None:
    rendered = render_apisix_yaml(TEMPLATE, BASE)
    hive = _route_block(rendered, "mcp-hive-http")
    livy = _route_block(rendered, "spark-livy")
    assert "uri: /mcp/hive*" in rendered
    assert "limit-count:" in hive
    assert "key: knox_user" in hive
    assert "group: mcp-hive" in hive
    assert "limit-count:" not in livy
    assert "uri: /cdp/hive" not in rendered
    webhdfs = _route_block(rendered, "hdfs-webhdfs")
    assert "uri: /cdp/webhdfs*" in rendered
    assert 'methods: ["GET", "HEAD", "PUT"]' in webhdfs
    assert "limit-count:" not in webhdfs
    assert "DELETE" not in webhdfs


def test_mcp_rate_count_rejects_zero() -> None:
    with pytest.raises(ValueError, match="MCP_RATE_COUNT"):
        render_apisix_yaml(TEMPLATE, {**BASE, "MCP_RATE_COUNT": "0"})
