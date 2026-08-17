from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_inventory_has_required_knox_fields() -> None:
    inventory = yaml.safe_load((ROOT / "inventory" / "cdp.yaml").read_text())
    knox = inventory["knox"]
    for key in (
        "issuer",
        "expected_alg",
        "topology",
        "proxy_prefix",
        "jwks_path",
        "token_api",
    ):
        assert knox[key], f"inventory knox.{key} must be set"
    assert knox["issuer"] == "KNOXSSO"
    assert knox["expected_alg"] == "RS256"
    assert knox["topology"] == "cdp-proxy-token"


def test_first_tools_are_read_only() -> None:
    inventory = yaml.safe_load((ROOT / "inventory" / "cdp.yaml").read_text())
    tools = inventory["first_tools"]
    assert tools, "Phase 0 needs at least one tool"
    for tool in tools:
        assert tool["access"] == "read"
        assert tool["method"] in {"GET", "HEAD", "POST"}
        assert tool["path"].startswith("/")
        if tool["method"] == "POST":
            assert tool["path"].startswith("/mcp/")


def test_threat_model_covers_auth_bypass_cases() -> None:
    inventory = yaml.safe_load((ROOT / "inventory" / "cdp.yaml").read_text())
    threat_ids = {item["id"] for item in inventory["threats"]}
    assert {
        "missing-auth",
        "alg-none",
        "hmac-confusion",
        "expired-token",
        "wrong-issuer",
        "direct-knox-bypass",
    } <= threat_ids


def test_env_example_documents_live_cdp_contract() -> None:
    text = (ROOT / ".env.example").read_text()
    for key in (
        "GATEWAY_MODE",
        "KNOX_ISSUER",
        "KNOX_PROXY_PREFIX",
        "UPSTREAM_HOST",
        "KNOX_JWKS_URL",
        "KNOX_TOKEN",
        "KNOX_SERVICES",
        "ADMIN_PORT",
    ):
        assert key in text


def test_identity_model_keeps_agent_separate_from_user() -> None:
    inventory = yaml.safe_load((ROOT / "inventory" / "cdp.yaml").read_text())
    assert inventory["identity"]["user_claim"] == "sub"
    assert inventory["identity"]["agent_identity"] == "separate-from-user"
