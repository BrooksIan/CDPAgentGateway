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


def test_connect_impyla_drops_unsupported_verify_cert() -> None:
    from agentgateway.impyla_compat import connect_impyla, filter_impyla_kwargs

    def connect(*, host, jwt, auth_mechanism, use_ssl, use_http_transport, http_path, port, timeout):
        return {"host": host, "jwt": jwt}

    kwargs = {
        "host": "knox.example",
        "port": 443,
        "auth_mechanism": "JWT",
        "jwt": "t",
        "use_ssl": True,
        "use_http_transport": True,
        "http_path": "env/cdp-proxy-token/hive",
        "timeout": 60,
        "verify_cert": False,
    }
    assert "verify_cert" not in filter_impyla_kwargs(connect, kwargs)
    assert connect_impyla(connect, kwargs)["jwt"] == "t"


def test_connect_impyla_requires_jwt_parameter() -> None:
    from agentgateway.impyla_compat import connect_impyla

    def connect(*, host):
        return host

    try:
        connect_impyla(connect, {"host": "knox.example", "jwt": "t"})
    except TypeError as extra:
        assert "impyla" in str(extra).lower()
    else:
        raise AssertionError("expected JWT requirement")
