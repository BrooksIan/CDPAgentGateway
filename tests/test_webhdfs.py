from __future__ import annotations

import pytest

from agentgateway.webhdfs import WebHdfsError, gateway_create_location, webhdfs_path


def test_webhdfs_path_normalizes_hdfs_uri() -> None:
    assert webhdfs_path("/user/analyst/job.py") == "/cdp/webhdfs/v1/user/analyst/job.py"
    assert webhdfs_path("hdfs:///user/analyst/job.py") == "/cdp/webhdfs/v1/user/analyst/job.py"
    assert webhdfs_path("user/analyst/job.py") == "/cdp/webhdfs/v1/user/analyst/job.py"


def test_create_location_rewrites_knox_prefix_to_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APISIX_PORT", "9080")
    env = {
        "UPSTREAM_HOST": "knox.example.cloudera.site",
        "KNOX_PROXY_PREFIX": "/env/cdp-proxy-token",
    }
    location = (
        "https://knox.example.cloudera.site:443/env/cdp-proxy-token/"
        "webhdfs/data/v1/webhdfs/v1/user/analyst/job.py?_=opaque"
    )
    rewritten = gateway_create_location(location, env)
    assert rewritten.startswith("http://127.0.0.1:9080/cdp/webhdfs/data/v1/webhdfs/v1/user/analyst/job.py")
    assert "_=opaque" in rewritten
    assert "knox.example.cloudera.site" not in rewritten.split("?", 1)[0]


def test_create_location_refuses_foreign_host() -> None:
    env = {"UPSTREAM_HOST": "knox.example.cloudera.site", "KNOX_PROXY_PREFIX": "/gateway/cdp-proxy-token"}
    with pytest.raises(WebHdfsError, match="refusing redirect host"):
        gateway_create_location("https://evil.example/webhdfs/data/v1/webhdfs/v1/user/x", env)
