from __future__ import annotations

from agentgateway.knox import (
    default_call_path,
    hive_jdbc_updates,
    http_url_from_jdbc,
    merge_knox_config,
    parse_hive_jdbc,
    parse_knox_proxy_url,
    redact_jdbc,
    require_token_topology,
    trusted_jku,
)
from agentgateway.token import inspect_bearer, unsigned_token


def test_parse_private_cloud_gateway_origin() -> None:
    parsed = parse_knox_proxy_url("https://knox.example.com:8443")
    assert parsed["GATEWAY_MODE"] == "live"
    assert parsed["UPSTREAM_HOST"] == "knox.example.com"
    assert parsed["UPSTREAM_PORT"] == "8443"
    assert parsed["KNOX_PROXY_PREFIX"] == "/gateway/cdp-proxy-token"
    assert parsed["KNOX_JWKS_URL"].endswith("/gateway/homepage/knoxtoken/api/v1/jwks.json")
    assert parsed["KNOX_PROXY_URL"] == "https://knox.example.com:8443/gateway/cdp-proxy-token"


def test_parse_strips_service_path_after_topology() -> None:
    parsed = parse_knox_proxy_url(
        "https://knox.example.com:8443/gateway/cdp-proxy-token/webhdfs/v1/?op=LISTSTATUS"
    )
    assert parsed["KNOX_PROXY_PREFIX"] == "/gateway/cdp-proxy-token"
    assert parsed["KNOX_SERVICE_PATH"] == "/webhdfs/v1"
    assert parsed["KNOX_PROXY_URL"] == "https://knox.example.com:8443/gateway/cdp-proxy-token/webhdfs/v1"


def test_parse_public_cloud_livy_spark3() -> None:
    parsed = parse_knox_proxy_url(
        "https://knox.example.cloudera.site:443/go01-obser-de/cdp-proxy-token/livy_for_spark3/"
    )
    assert parsed["UPSTREAM_PORT"] == "443"
    assert parsed["KNOX_PROXY_PREFIX"] == "/go01-obser-de/cdp-proxy-token"
    assert parsed["KNOX_SERVICE_PATH"] == "/livy_for_spark3"
    assert parsed["KNOX_PROXY_URL"] == (
        "https://knox.example.cloudera.site/go01-obser-de/cdp-proxy-token/livy_for_spark3"
    )
    assert default_call_path(parsed) == "/cdp/livy_for_spark3/sessions"


def test_parse_public_cloud_env_prefix() -> None:
    parsed = parse_knox_proxy_url("https://knox.example.cloudera.site/lake-1/cdp-proxy-token")
    assert parsed["UPSTREAM_PORT"] == "443"
    assert parsed["KNOX_PROXY_PREFIX"] == "/lake-1/cdp-proxy-token"
    assert parsed["KNOX_JWKS_URL"] == (
        "https://knox.example.cloudera.site/lake-1/homepage/knoxtoken/api/v1/jwks.json"
    )


def test_parse_hive_jdbc_http() -> None:
    jdbc = (
        "jdbc:hive2://knox.example.cloudera.site:443/;"
        "ssl=true;transportMode=http;httpPath=go01-obser-de/cdp-proxy-token/hive"
    )
    assert http_url_from_jdbc(jdbc) == (
        "https://knox.example.cloudera.site:443/go01-obser-de/cdp-proxy-token/hive"
    )
    parsed = parse_knox_proxy_url(jdbc)
    assert parsed["UPSTREAM_HOST"] == "knox.example.cloudera.site"
    assert parsed["UPSTREAM_PORT"] == "443"
    assert parsed["UPSTREAM_SCHEME"] == "https"
    assert parsed["KNOX_PROXY_PREFIX"] == "/go01-obser-de/cdp-proxy-token"
    assert parsed["KNOX_SERVICE_PATH"] == "/hive"
    assert parsed["KNOX_SERVICES"] == "/hive"


def test_hive_jdbc_requires_http_transport() -> None:
    try:
        http_url_from_jdbc("jdbc:hive2://knox.example.com:10000/default")
    except ValueError as exc:
        assert "transportMode=http" in str(exc)
    else:
        raise AssertionError("expected binary JDBC rejection")


def test_parse_hive_jdbc_cdp_proxy_api() -> None:
    jdbc = (
        "jdbc:hive2://go01-obser-de-gateway.go01-dem.ylcu-atmi.cloudera.site/;"
        "ssl=true;transportMode=http;httpPath=go01-obser-de/cdp-proxy-api/hive"
    )
    assert http_url_from_jdbc(jdbc) == (
        "https://go01-obser-de-gateway.go01-dem.ylcu-atmi.cloudera.site:443"
        "/go01-obser-de/cdp-proxy-api/hive"
    )
    hive = parse_hive_jdbc(jdbc)
    assert hive["HIVE_KNOX_TOPOLOGY"] == "cdp-proxy-api"
    assert hive["HIVE_KNOX_PREFIX"] == "/go01-obser-de/cdp-proxy-api"
    assert hive["HIVE_KNOX_SERVICE"] == "/hive"
    assert hive["HIVE_KNOX_URL"] == (
        "https://go01-obser-de-gateway.go01-dem.ylcu-atmi.cloudera.site"
        "/go01-obser-de/cdp-proxy-api/hive"
    )
    assert hive["KNOX_JWKS_URL"] == (
        "https://go01-obser-de-gateway.go01-dem.ylcu-atmi.cloudera.site"
        "/go01-obser-de/homepage/knoxtoken/api/v1/jwks.json"
    )


def test_cdp_proxy_api_does_not_match_cdp_proxy() -> None:
    parsed = parse_knox_proxy_url(
        "https://knox.example.cloudera.site/go01-obser-de/cdp-proxy-api/hive"
    )
    assert parsed["KNOX_TOPOLOGY"] == "cdp-proxy-api"
    assert parsed["KNOX_PROXY_PREFIX"] == "/go01-obser-de/cdp-proxy-api"
    assert parsed["KNOX_SERVICE_PATH"] == "/hive"


def test_require_token_topology_rejects_api_topology() -> None:
    parsed = parse_knox_proxy_url(
        "jdbc:hive2://knox.example.cloudera.site/;"
        "ssl=true;transportMode=http;httpPath=env/cdp-proxy-api/hive"
    )
    try:
        require_token_topology(parsed)
    except ValueError as exc:
        assert "gateway jdbc add" in str(exc)
    else:
        raise AssertionError("expected cdp-proxy-api rejection on knox pin")


def test_jdbc_add_does_not_replace_livy_prefix() -> None:
    livy = parse_knox_proxy_url(
        "https://go01-obser-de-gateway.go01-dem.ylcu-atmi.cloudera.site"
        "/go01-obser-de/cdp-proxy-token/livy_for_spark3/"
    )
    jdbc = (
        "jdbc:hive2://go01-obser-de-gateway.go01-dem.ylcu-atmi.cloudera.site/;"
        "ssl=true;transportMode=http;httpPath=go01-obser-de/cdp-proxy-api/hive"
    )
    updates = hive_jdbc_updates(livy, jdbc)
    assert "KNOX_PROXY_PREFIX" not in updates
    assert updates["HIVE_KNOX_PREFIX"] == "/go01-obser-de/cdp-proxy-api"
    assert updates["HIVE_KNOX_TOPOLOGY"] == "cdp-proxy-api"


def test_jdbc_add_rejects_other_host() -> None:
    livy = parse_knox_proxy_url("https://knox.example.cloudera.site/env/cdp-proxy-token/livy_for_spark3/")
    jdbc = (
        "jdbc:hive2://other.example.cloudera.site/;"
        "ssl=true;transportMode=http;httpPath=env/cdp-proxy-api/hive"
    )
    try:
        hive_jdbc_updates(livy, jdbc)
    except ValueError as exc:
        assert "does not match pinned Knox host" in str(exc)
    else:
        raise AssertionError("expected host mismatch")


def test_redact_jdbc_password() -> None:
    jdbc = (
        "jdbc:hive2://knox.example.cloudera.site/;"
        "ssl=true;password=s3cret;transportMode=http;httpPath=env/cdp-proxy-api/hive"
    )
    redacted = redact_jdbc(jdbc)
    assert "s3cret" not in redacted
    assert "password=***" in redacted


def test_merge_keeps_livy_when_adding_hive() -> None:
    livy = parse_knox_proxy_url(
        "https://knox.example.cloudera.site/go01-obser-de/cdp-proxy-token/livy_for_spark3/"
    )
    hive = parse_knox_proxy_url(
        "jdbc:hive2://knox.example.cloudera.site:443/;"
        "ssl=true;transportMode=http;httpPath=go01-obser-de/cdp-proxy-token/hive"
    )
    merged = merge_knox_config(livy, hive)
    assert merged["KNOX_SERVICE_PATH"] == "/livy_for_spark3"
    assert merged["KNOX_SERVICES"] == "/livy_for_spark3,/hive"
    assert merged["KNOX_PROXY_URL"] == (
        "https://knox.example.cloudera.site/go01-obser-de/cdp-proxy-token"
    )


def test_trusted_jku_rejects_foreign_host() -> None:
    try:
        trusted_jku("https://evil.example/jwks.json", "knox.example.com")
    except ValueError as exc:
        assert "evil.example" in str(exc)
    else:
        raise AssertionError("expected host pin failure")


def test_inspect_bearer_rejects_unsigned_token() -> None:
    try:
        inspect_bearer(unsigned_token())
    except ValueError as exc:
        assert "RS256" in str(exc)
    else:
        raise AssertionError("expected alg rejection")


def test_help_includes_knox() -> None:
    from test_cli import run_gateway

    result = run_gateway("knox", "--help")
    assert result.returncode == 0
    assert "cdp-proxy-token" in result.stdout
