from __future__ import annotations

from pathlib import Path

import pytest

from agentgateway.project_knox import (
    KNOX_PROXY_PLACEHOLDER,
    apply_inventory_knox,
    configure_project_knox,
    is_unconfigured_knox_url,
    knox_urls_from_inventory,
    pending_knox_updates,
)

ROOT = Path(__file__).resolve().parents[1]


def test_inventory_builds_livy_and_jwks_urls() -> None:
    urls = knox_urls_from_inventory(ROOT)
    assert urls["KNOX_PROXY_URL"].startswith("https://")
    assert urls["KNOX_PROXY_URL"].endswith("/cdp-proxy-token/livy_for_spark3/")
    assert urls["KNOX_JWKS_URL"].endswith("/homepage/knoxtoken/api/v2/jwks.json")
    assert "knox.invalid" not in urls["KNOX_PROXY_URL"]


def test_placeholder_is_replaced_and_override_is_kept() -> None:
    inventory = knox_urls_from_inventory(ROOT)
    pending = pending_knox_updates(
        {"KNOX_PROXY_URL": KNOX_PROXY_PLACEHOLDER, "KNOX_JWKS_URL": ""},
        inventory,
    )
    assert pending == inventory
    override = "https://other.example/env/cdp-proxy-token/livy_for_spark3/"
    assert pending_knox_updates({"KNOX_PROXY_URL": override, "KNOX_JWKS_URL": ""}, inventory) == {}
    assert is_unconfigured_knox_url("")
    assert is_unconfigured_knox_url(KNOX_PROXY_PLACEHOLDER)
    assert not is_unconfigured_knox_url(override)


def test_apply_inventory_knox_fills_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KNOX_PROXY_URL", KNOX_PROXY_PLACEHOLDER)
    monkeypatch.setenv("KNOX_JWKS_URL", "")
    updates = apply_inventory_knox(ROOT)
    assert updates["KNOX_PROXY_URL"].endswith("/livy_for_spark3/")
    assert "knox.invalid" not in updates["KNOX_PROXY_URL"]


def test_configure_project_knox_publishes_without_dropping_other_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KNOX_PROXY_URL", KNOX_PROXY_PLACEHOLDER)
    monkeypatch.setenv("KNOX_JWKS_URL", "")
    monkeypatch.setenv("ENABLE_MCP_SPARK", "true")
    monkeypatch.delenv("CDSW_PROJECT_ID", raising=False)

    class Project:
        def __init__(self, environment: dict[str, str]):
            self.environment = environment

    class Client:
        def __init__(self) -> None:
            self.environment = {
                "KNOX_PROXY_URL": KNOX_PROXY_PLACEHOLDER,
                "ENABLE_MCP_IMPALA": "false",
            }
            self.updated = None

        def get_project(self, project_id: str) -> Project:
            assert project_id == "proj-1"
            return Project(dict(self.environment))

        def update_project(self, body, project_id: str) -> None:
            assert project_id == "proj-1"
            self.updated = body

    monkeypatch.setattr(
        "agentgateway.project_knox._update_body",
        lambda environment: {"environment": environment},
    )
    client = Client()
    updates = configure_project_knox(ROOT, client=client, project_id="proj-1")
    assert updates["KNOX_PROXY_URL"].endswith("/livy_for_spark3/")
    written = client.updated["environment"]
    assert written["KNOX_PROXY_URL"] == updates["KNOX_PROXY_URL"]
    assert written["KNOX_JWKS_URL"] == updates["KNOX_JWKS_URL"]
    assert written["ENABLE_MCP_IMPALA"] == "false"
    assert written["ENABLE_MCP_SPARK"] == "true"


def test_configure_project_knox_skips_publish_for_an_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "KNOX_PROXY_URL",
        "https://other.example/env/cdp-proxy-token/livy_for_spark3/",
    )

    class Client:
        def get_project(self, project_id: str):
            raise AssertionError("override must not read project settings")

    assert configure_project_knox(ROOT, client=Client(), project_id="proj-1") == {}
