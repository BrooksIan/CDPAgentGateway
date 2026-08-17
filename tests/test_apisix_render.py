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


def test_oauth_prm_and_token_state_are_rendered() -> None:
    rendered = render_apisix_yaml(TEMPLATE, {**BASE, "GATEWAY_MODE": "local"})
    assert "oauth-protected-resource" in rendered
    assert "resource_metadata:" in rendered
    assert "token_state_url: \"http://mock-cdp:8080/gateway/homepage/knoxtoken/api/v2/token/state\"" in rendered
    livy = _route_block(rendered, "spark-livy")
    assert "key-auth:" not in livy


def test_mcp_key_auth_is_optional() -> None:
    off = render_apisix_yaml(TEMPLATE, BASE)
    assert "key-auth:" not in off
    on = render_apisix_yaml(TEMPLATE, {**BASE, "AGENT_CALLER_KEY": "lab-agent"})
    mcp = _route_block(on, "mcp-spark-http")
    livy = _route_block(on, "spark-livy")
    assert "key-auth:" in mcp
    assert "X-Agent-Key" in mcp
    assert "hide_credentials: true" in mcp
    assert "key-auth:" not in livy
    assert "username: agent-platform" in on


def test_live_token_state_url_must_match_upstream_host() -> None:
    with pytest.raises(ValueError, match="KNOX_TOKEN_STATE_URL"):
        render_apisix_yaml(
            TEMPLATE,
            {
                **BASE,
                "GATEWAY_MODE": "live",
                "UPSTREAM_HOST": "knox.example.cloudera.site",
                "KNOX_TOKEN_STATE_URL": "http://evil.example/token/state",
            },
        )
