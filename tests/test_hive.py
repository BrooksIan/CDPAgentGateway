from __future__ import annotations

from agentgateway.hive import HiveError, hive_connect_kwargs, hive_http_path


def test_hive_http_path_uses_token_topology() -> None:
    path = hive_http_path({"KNOX_PROXY_PREFIX": "/go01-obser-de/cdp-proxy-token"})
    assert path == "go01-obser-de/cdp-proxy-token/hive"


def test_hive_http_path_rejects_api_topology() -> None:
    try:
        hive_http_path({"KNOX_PROXY_PREFIX": "/go01-obser-de/cdp-proxy-api"})
    except HiveError as exc:
        assert "cdp-proxy-token" in str(exc)
    else:
        raise AssertionError("expected token-topology requirement")


def test_hive_connect_kwargs_use_jwt_and_skip_verify() -> None:
    kwargs = hive_connect_kwargs(
        {
            "UPSTREAM_HOST": "knox.example.cloudera.site",
            "UPSTREAM_PORT": "443",
            "UPSTREAM_SCHEME": "https",
            "UPSTREAM_TLS_VERIFY": "false",
            "KNOX_PROXY_PREFIX": "/env/cdp-proxy-token",
        },
        "dummy-token",
    )
    assert kwargs["auth_mechanism"] == "JWT"
    assert kwargs["jwt"] == "dummy-token"
    assert kwargs["http_path"] == "env/cdp-proxy-token/hive"
    assert kwargs["verify_cert"] is False
    assert "dummy-token" not in kwargs["http_path"]
