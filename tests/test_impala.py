from __future__ import annotations

from agentgateway.impala import ImpalaError, impala_connect_kwargs, impala_http_path, impala_target


def test_impala_http_path_uses_token_topology() -> None:
    path = impala_http_path({"KNOX_PROXY_PREFIX": "/go01-obser-de/cdp-proxy-token"})
    assert path == "go01-obser-de/cdp-proxy-token/impala"


def test_impala_http_path_rejects_api_topology() -> None:
    try:
        impala_http_path({"KNOX_PROXY_PREFIX": "/go01-obser-de/cdp-proxy-api"})
    except ImpalaError as exc:
        assert "cdp-proxy-token" in str(exc)
    else:
        raise AssertionError("expected token-topology requirement")


def test_impala_connect_kwargs_use_jwt_and_skip_verify() -> None:
    kwargs = impala_connect_kwargs(
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
    assert kwargs["http_path"] == "env/cdp-proxy-token/impala"
    assert kwargs["verify_cert"] is False
    assert "dummy-token" not in kwargs["http_path"]


def test_impala_connect_kwargs_prefer_cdw_coordinator() -> None:
    kwargs = impala_connect_kwargs(
        {
            "UPSTREAM_HOST": "knox.example.cloudera.site",
            "UPSTREAM_PORT": "443",
            "UPSTREAM_SCHEME": "https",
            "KNOX_PROXY_PREFIX": "/env/cdp-proxy-token",
            "IMPALA_HOST": "coordinator-default-impala-aws.dw-go01-demo-aws.ylcu-atmi.cloudera.site",
            "IMPALA_PORT": "443",
            "IMPALA_SCHEME": "https",
            "IMPALA_HTTP_PATH": "cliservice",
            "IMPALA_TLS_VERIFY": "false",
        },
        "dummy-token",
    )
    assert kwargs["host"].startswith("coordinator-default-impala-aws")
    assert kwargs["http_path"] == "cliservice"
    assert kwargs["auth_mechanism"] == "JWT"
    assert kwargs["jwt"] == "dummy-token"
    assert "cdp-proxy-token" not in kwargs["http_path"]
    assert kwargs["host"] != "knox.example.cloudera.site"
    env = {
        "IMPALA_HOST": kwargs["host"],
        "IMPALA_PORT": "443",
        "IMPALA_SCHEME": "https",
        "IMPALA_HTTP_PATH": "cliservice",
    }
    assert impala_http_path(env) == "cliservice"
    assert impala_target(env).startswith("https://coordinator-default-impala-aws")
    assert "dummy-token" not in impala_target(env)
