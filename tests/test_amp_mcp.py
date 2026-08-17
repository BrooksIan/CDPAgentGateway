from __future__ import annotations

from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from agentgateway.amp import (
    BurstLimiter,
    KnoxJWTMiddleware,
    build_impala_mcp_app,
    build_mcp_app,
    cml_bind_host,
    event_loop_running,
)
from agentgateway.keys import generate_test_keys
from agentgateway.token import knox_claims, sign_rs256, unsigned_token

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def pem_file() -> Path:
    generate_test_keys()
    return ROOT / "conf" / "keys" / "public.pem"


def _wrapped(pem_file: Path, *, count: int = 60) -> TestClient:
    async def echo(request):
        return JSONResponse(
            {
                "knox_user": request.headers.get("x-knox-user"),
                "token_id": request.headers.get("x-knox-token-id"),
                "request_id": request.headers.get("x-request-id"),
            }
        )

    async def health(_request):
        return JSONResponse({"status": "ok"})

    inner = Starlette(routes=[Route("/health", health, methods=["GET"]), Route("/echo", echo, methods=["POST"])])
    app = KnoxJWTMiddleware(inner, public_key_file=pem_file, limiter=BurstLimiter(count, 60))
    return TestClient(app)


def test_amp_health_is_public(pem_file: Path) -> None:
    client = _wrapped(pem_file)
    response = client.get("/health")
    assert response.status_code == 200


def test_amp_healthcheck_and_head_skip_jwt(pem_file: Path) -> None:
    inner = Starlette(
        routes=[
            Route("/healthcheck", lambda _r: JSONResponse({"status": "ok"}), methods=["GET", "HEAD"]),
        ]
    )
    app = KnoxJWTMiddleware(inner, public_key_file=pem_file)
    client = TestClient(app)
    assert client.get("/healthcheck").status_code == 200
    assert client.head("/healthcheck").status_code == 200


@pytest.mark.parametrize(
    ("headers", "reason"),
    [
        ({}, "missing_token"),
        ({"Authorization": "Basic Zm9vOmJhcg=="}, "missing_token"),
        ({"Authorization": "Bearer"}, "missing_token"),
        ({"Authorization": "Bearer not-a-jwt"}, "invalid_token"),
    ],
)
def test_amp_missing_or_malformed_token(pem_file: Path, headers, reason) -> None:
    response = _wrapped(pem_file).post("/echo", headers=headers)
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate", "").startswith("Bearer")
    assert response.json()["reason"] == reason
    assert "authorization" not in response.text.lower() or "bearer eyj" not in response.text.lower()


def test_amp_alg_none_and_hs256(pem_file: Path) -> None:
    none = _wrapped(pem_file).post("/echo", headers={"Authorization": f"Bearer {unsigned_token()}"})
    assert none.status_code == 401
    assert none.json()["reason"] == "invalid_alg"


def test_amp_valid_token_sets_identity_headers(pem_file: Path) -> None:
    token = sign_rs256(knox_claims(sub="analyst"))
    response = _wrapped(pem_file).post("/echo", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert body["knox_user"] == "analyst"
    assert body["token_id"] == "test-token-id"
    assert body["request_id"]


def test_amp_burst_cap(pem_file: Path) -> None:
    client = _wrapped(pem_file, count=2)
    token = sign_rs256(knox_claims(sub="analyst"))
    headers = {"Authorization": f"Bearer {token}"}
    assert client.post("/echo", headers=headers).status_code == 200
    assert client.post("/echo", headers=headers).status_code == 200
    limited = client.post("/echo", headers=headers)
    assert limited.status_code == 429
    assert limited.json()["reason"] == "rate_limited"


def test_amp_mcp_tools_list(pem_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KNOX_PROXY_URL", "https://knox.example.com/gateway/cdp-proxy-token/livy_for_spark3/")
    monkeypatch.setenv("KNOX_PUBLIC_KEY_FILE", str(pem_file))
    monkeypatch.setenv("ADMIN_BACKEND", "sqlite")
    monkeypatch.setenv("ADMIN_DB", str(tmp_path / "gateway.sqlite"))
    client = TestClient(build_mcp_app())
    public = client.get("/health")
    assert public.status_code == 200
    prm = client.get("/.well-known/oauth-protected-resource")
    assert prm.status_code == 200
    assert prm.json()["bearer_methods_supported"] == ["header"]
    denied = client.post("/mcp/spark", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert denied.status_code == 401
    token = sign_rs256(knox_claims(sub="analyst"))
    listed = client.post(
        "/mcp/spark",
        headers={"Authorization": f"Bearer {token}"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert listed.status_code == 200, listed.text
    names = [tool["name"] for tool in listed.json()["result"]["tools"]]
    assert "spark_submit_batch" in names
    assert "Authorization" not in listed.text


def test_amp_impala_mcp_tools_list(pem_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KNOX_PROXY_URL", "https://knox.example.com/gateway/cdp-proxy-token/livy_for_spark3/")
    monkeypatch.setenv("KNOX_PUBLIC_KEY_FILE", str(pem_file))
    monkeypatch.setenv("ADMIN_BACKEND", "sqlite")
    monkeypatch.setenv("ADMIN_DB", str(tmp_path / "gateway.sqlite"))
    client = TestClient(build_impala_mcp_app())
    public = client.get("/health")
    assert public.status_code == 200
    denied = client.post("/mcp/impala", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert denied.status_code == 401
    token = sign_rs256(knox_claims(sub="analyst"))
    listed = client.post(
        "/mcp/impala",
        headers={"Authorization": f"Bearer {token}"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert listed.status_code == 200, listed.text
    names = [tool["name"] for tool in listed.json()["result"]["tools"]]
    assert "impala_select" in names
    assert "Authorization" not in listed.text


def test_amp_prm_is_public_and_401_has_resource_metadata(pem_file: Path) -> None:
    client = _wrapped(pem_file)
    denied = client.post("/echo")
    assert denied.status_code == 401
    assert "resource_metadata=" in denied.headers.get("WWW-Authenticate", "")


def test_cml_bind_host_is_loopback_when_app_port_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CDSW_APP_PORT", raising=False)
    assert cml_bind_host() == "0.0.0.0"
    monkeypatch.setenv("CDSW_APP_PORT", "8090")
    assert cml_bind_host() == "127.0.0.1"


def test_event_loop_running_is_false_in_sync_tests() -> None:
    assert event_loop_running() is False
