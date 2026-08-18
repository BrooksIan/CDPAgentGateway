"""Cloudera AI / CML AMP runtime: Python Knox JWT in front of mcp-spark, mcp-hive, and mcp-impala.

Compose still uses APISIX + knox-jwt.lua. This profile is optional and live-Knox only.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from agentgateway.knox import parse_knox_proxy_url
from agentgateway.knox_jwt import (
    DEFAULT_ALG,
    DEFAULT_CLOCK_SKEW,
    DEFAULT_ISSUER,
    DEFAULT_REALM,
    KnoxJWTError,
    assert_knox_token_enabled,
    extract_bearer,
    unauthorized_headers,
    verify_knox_jwt,
)
from agentgateway.paths import repo_root


def cml_port() -> int:
    raw = os.environ.get("CDSW_APP_PORT") or os.environ.get("PORT") or "8080"
    try:
        return int(raw)
    except ValueError:
        return 8080


def cml_bind_host() -> str:
    """CML's proxy probes 127.0.0.1:CDSW_APP_PORT. Bind 0.0.0.0 only off-workbench."""
    if os.environ.get("CDSW_APP_PORT"):
        return "127.0.0.1"
    return "0.0.0.0"


def amp_public_key_path() -> Path:
    configured = os.environ.get("KNOX_PUBLIC_KEY_FILE") or ""
    if configured:
        path = Path(configured)
        return path if path.is_absolute() else repo_root() / path
    return repo_root() / "conf" / "generated" / "knox-public.pem"


def apply_live_upstream() -> dict[str, str]:
    url = (os.environ.get("KNOX_PROXY_URL") or "").strip()
    if not url:
        raise ValueError("AMP requires KNOX_PROXY_URL (Knox Livy-for-Spark-3 HTTPS URL)")
    parsed = parse_knox_proxy_url(url)
    for key, value in parsed.items():
        os.environ.setdefault(key, value)
    os.environ.setdefault("ADMIN_BACKEND", "sqlite")
    os.environ.setdefault("ADMIN_DB", str(repo_root() / "data" / "gateway.sqlite"))
    os.environ.setdefault("GATEWAY_MODE", "live")
    admin = str(repo_root() / "admin")
    if admin not in sys.path:
        sys.path.append(admin)
    return parsed


def ensure_amp_runtime_pem() -> Path:
    """Pin Knox JWKS when the Fetch JWKS job did not write a PEM yet."""
    path = amp_public_key_path()
    if path.is_file() and path.read_text().strip():
        return path
    from agentgateway.amp_apisix import ensure_amp_knox_pem

    return ensure_amp_knox_pem()


def _ensure_service_path(relative: str) -> str:
    path = str(repo_root() / relative)
    if path in sys.path:
        sys.path.remove(path)
    sys.path.insert(0, path)
    return path


def _load_module(module_name: str, directory: str, filename: str = "server.py"):
    import importlib.util

    path = _ensure_service_path(directory)
    file_path = repo_root() / directory / filename
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    displaced: dict[str, object] = {}
    try:
        for name in ("sql", "hs2", "quota", "livy"):
            cached = sys.modules.get(name)
            file = getattr(cached, "__file__", "") or ""
            if cached is not None and not file.startswith(path):
                displaced[name] = cached
                sys.modules.pop(name, None)
        spec.loader.exec_module(module)
        return module
    finally:
        if sys.path and sys.path[0] == path:
            sys.path.pop(0)
        for name in ("sql", "hs2", "quota"):
            cached = sys.modules.get(name)
            file = getattr(cached, "__file__", "") or ""
            if cached is not None and file.startswith(path):
                sys.modules.pop(name, None)
        sys.modules.update(displaced)


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name) or str(default)
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


class BurstLimiter:
    def __init__(self, count: int, window: int):
        self.count = max(int(count), 0)
        self.window = max(int(window), 1)
        self._hits: dict[str, list[float]] = defaultdict(list)

    def allow(self, sub: str, *, now: float | None = None) -> bool:
        if self.count <= 0:
            return True
        stamp = now if now is not None else time.time()
        bucket = self._hits[sub]
        cutoff = stamp - self.window
        self._hits[sub] = [hit for hit in bucket if hit > cutoff]
        if len(self._hits[sub]) >= self.count:
            return False
        self._hits[sub].append(stamp)
        return True


def _header_map(scope: dict[str, Any]) -> dict[bytes, bytes]:
    return {key: value for key, value in scope.get("headers") or []}


def _set_header(scope: dict[str, Any], name: str, value: str) -> None:
    key = name.lower().encode("latin-1")
    raw = str(value).encode("latin-1", "replace")
    headers = [pair for pair in scope.get("headers") or [] if pair[0] != key]
    headers.append((key, raw))
    scope["headers"] = headers


async def _send_json(
    send,
    *,
    status: int,
    body: dict[str, Any],
    extra_headers: list[tuple[bytes, bytes]] | None = None,
) -> None:
    payload = json.dumps(body).encode("utf-8")
    headers = list(extra_headers or [])
    if not any(key == b"content-type" for key, _ in headers):
        headers.append((b"content-type", b"application/json"))
    headers.append((b"content-length", str(len(payload)).encode("ascii")))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": payload})


class KnoxJWTMiddleware:
    """ASGI wrapper: skip public GETs, require Knox JWT on MCP POSTs."""

    def __init__(self, app, *, public_key_file: Path, limiter: BurstLimiter | None = None):
        self.app = app
        self.public_key_file = public_key_file
        self.limiter = limiter
        self.issuer = os.environ.get("KNOX_ISSUER") or DEFAULT_ISSUER
        self.expected_alg = os.environ.get("KNOX_EXPECTED_ALG") or DEFAULT_ALG
        self.clock_skew = _int_env("KNOX_CLOCK_SKEW", DEFAULT_CLOCK_SKEW)
        self.realm = os.environ.get("KNOX_REALM") or DEFAULT_REALM
        if os.environ.get("RESOURCE_METADATA_URL"):
            self.resource_metadata = os.environ["RESOURCE_METADATA_URL"].strip()
        elif os.environ.get("GATEWAY_PUBLIC_URL"):
            self.resource_metadata = (
                os.environ["GATEWAY_PUBLIC_URL"].rstrip("/")
                + "/.well-known/oauth-protected-resource"
            )
        else:
            self.resource_metadata = "http://127.0.0.1:9080/.well-known/oauth-protected-resource"
        self.token_state_url = (os.environ.get("KNOX_TOKEN_STATE_URL") or "").strip()
        self.token_state_host = (os.environ.get("UPSTREAM_HOST") or "").strip()
        self.token_state_tls_verify = os.environ.get("UPSTREAM_TLS_VERIFY", "false").lower() in {
            "true",
            "1",
            "yes",
        }
        self._pem: str | None = None

    def _public_key(self) -> str:
        if self._pem is None:
            if not self.public_key_file.is_file():
                raise KnoxJWTError("gateway_misconfigured", status=500)
            pem = self.public_key_file.read_text()
            if not pem.strip():
                raise KnoxJWTError("gateway_misconfigured", status=500)
            self._pem = pem
        return self._pem

    def _skip_jwt(self, method: str, path: str) -> bool:
        if method not in {"GET", "HEAD"}:
            return False
        normalized = path.rstrip("/") or "/"
        if normalized in {"/", "/health", "/healthcheck"}:
            return True
        return path.startswith("/.well-known/oauth-protected-resource")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = scope.get("method") or "GET"
        path = scope.get("path") or "/"
        if self._skip_jwt(method, path):
            await self.app(scope, receive, send)
            return
        headers = _header_map(scope)
        authorization = headers.get(b"authorization", b"").decode("latin-1")
        try:
            token = extract_bearer(authorization)
            identity = verify_knox_jwt(
                token,
                public_key_pem=self._public_key(),
                issuer=self.issuer,
                expected_alg=self.expected_alg,
                clock_skew=self.clock_skew,
            )
            assert_knox_token_enabled(
                identity,
                token_state_url=self.token_state_url,
                token_state_host=self.token_state_host,
                tls_verify=self.token_state_tls_verify,
            )
        except KnoxJWTError as extra:
            headers = unauthorized_headers(
                realm=self.realm,
                reason=extra.reason,
                resource_metadata=self.resource_metadata,
            ) if extra.status == 401 else [
                (b"x-agent-gateway-reason", extra.reason.encode("ascii")),
                (b"content-type", b"application/json"),
            ]
            await _send_json(
                send,
                status=extra.status,
                body={"error": "unauthorized" if extra.status == 401 else "gateway_misconfigured", "reason": extra.reason},
                extra_headers=headers,
            )
            return
        if self.limiter and method == "POST" and not self.limiter.allow(identity.sub):
            await _send_json(
                send,
                status=429,
                body={"error": "rate_limited", "reason": "rate_limited"},
                extra_headers=[
                    (b"x-agent-gateway-reason", b"rate_limited"),
                    (b"content-type", b"application/json"),
                ],
            )
            return
        request_id = headers.get(b"x-request-id", b"").decode("latin-1").strip() or uuid.uuid4().hex
        try:
            _set_header(scope, "X-Knox-User", identity.sub)
            if identity.token_id:
                _set_header(scope, "X-Knox-Token-Id", identity.token_id)
            _set_header(scope, "X-Request-Id", request_id)
            await self.app(scope, receive, send)
        except Exception as extra:  # noqa: BLE001 — never return uvicorn's plain Internal Server Error
            print(
                json.dumps(
                    {
                        "service": "agent-gateway",
                        "event": "unhandled",
                        "error": type(extra).__name__,
                        "detail": str(extra)[:200],
                    }
                ),
                flush=True,
            )
            await _send_json(
                send,
                status=500,
                body={"error": "internal_error", "reason": type(extra).__name__},
            )


def _prm_routes() -> list[Route]:
    from agentgateway.env import load_env, oauth_prm_document

    async def prm(_request: Request) -> JSONResponse:
        return JSONResponse(oauth_prm_document(load_env()))

    paths = (
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/mcp/spark",
        "/.well-known/oauth-protected-resource/mcp/hive",
        "/.well-known/oauth-protected-resource/mcp/impala",
    )
    return [Route(path, prm, methods=["GET"]) for path in paths]


def build_mcp_app() -> Starlette:
    apply_live_upstream()
    ensure_amp_runtime_pem()
    mcp = _load_module("amp_mcp_spark_server", "mcp-spark")

    async def root(request: Request) -> Response:
        if request.method == "GET":
            return JSONResponse({"status": "ok", "service": "mcp-spark", "profile": "amp"})
        return await mcp.mcp_endpoint(request)

    app = Starlette(
        routes=[
            *_prm_routes(),
            Route("/health", mcp.health, methods=["GET", "HEAD"]),
            Route("/healthcheck", mcp.health, methods=["GET", "HEAD"]),
            Route("/", root, methods=["GET", "HEAD", "POST"]),
            Route("/mcp", mcp.mcp_endpoint, methods=["GET", "POST"]),
            Route("/mcp/", mcp.mcp_endpoint, methods=["GET", "POST"]),
            Route("/mcp/spark", mcp.mcp_endpoint, methods=["GET", "POST"]),
            Route("/mcp/spark/", mcp.mcp_endpoint, methods=["GET", "POST"]),
        ]
    )
    limiter = BurstLimiter(
        _int_env("MCP_RATE_COUNT", 60),
        _int_env("MCP_RATE_WINDOW", 60),
    )
    return KnoxJWTMiddleware(app, public_key_file=amp_public_key_path(), limiter=limiter)


def build_hive_mcp_app() -> Starlette:
    apply_live_upstream()
    ensure_amp_runtime_pem()
    mcp = _load_module("amp_mcp_hive_server", "mcp-hive")

    async def root(request: Request) -> Response:
        if request.method == "GET":
            return JSONResponse({"status": "ok", "service": "mcp-hive", "profile": "amp"})
        return await mcp.mcp_endpoint(request)

    app = Starlette(
        routes=[
            *_prm_routes(),
            Route("/health", mcp.health, methods=["GET", "HEAD"]),
            Route("/healthcheck", mcp.health, methods=["GET", "HEAD"]),
            Route("/", root, methods=["GET", "HEAD", "POST"]),
            Route("/mcp", mcp.mcp_endpoint, methods=["GET", "POST"]),
            Route("/mcp/", mcp.mcp_endpoint, methods=["GET", "POST"]),
            Route("/mcp/hive", mcp.mcp_endpoint, methods=["GET", "POST"]),
            Route("/mcp/hive/", mcp.mcp_endpoint, methods=["GET", "POST"]),
        ]
    )
    limiter = BurstLimiter(
        _int_env("MCP_RATE_COUNT", 60),
        _int_env("MCP_RATE_WINDOW", 60),
    )
    return KnoxJWTMiddleware(app, public_key_file=amp_public_key_path(), limiter=limiter)


def build_impala_mcp_app() -> Starlette:
    apply_live_upstream()
    ensure_amp_runtime_pem()
    mcp = _load_module("amp_mcp_impala_server", "mcp-impala")

    async def root(request: Request) -> Response:
        if request.method == "GET":
            return JSONResponse({"status": "ok", "service": "mcp-impala", "profile": "amp"})
        return await mcp.mcp_endpoint(request)

    app = Starlette(
        routes=[
            *_prm_routes(),
            Route("/health", mcp.health, methods=["GET", "HEAD"]),
            Route("/healthcheck", mcp.health, methods=["GET", "HEAD"]),
            Route("/", root, methods=["GET", "HEAD", "POST"]),
            Route("/mcp", mcp.mcp_endpoint, methods=["GET", "POST"]),
            Route("/mcp/", mcp.mcp_endpoint, methods=["GET", "POST"]),
            Route("/mcp/impala", mcp.mcp_endpoint, methods=["GET", "POST"]),
            Route("/mcp/impala/", mcp.mcp_endpoint, methods=["GET", "POST"]),
        ]
    )
    limiter = BurstLimiter(
        _int_env("MCP_RATE_COUNT", 60),
        _int_env("MCP_RATE_WINDOW", 60),
    )
    return KnoxJWTMiddleware(app, public_key_file=amp_public_key_path(), limiter=limiter)


def build_admin_app():
    os.environ.setdefault("ADMIN_DB", str(repo_root() / "data" / "gateway.sqlite"))
    os.environ.setdefault("APISIX_HEALTH_URL", "")
    os.environ.setdefault("MCP_SPARK_HEALTH_URL", os.environ.get("AMP_MCP_HEALTH_URL") or "")
    return _load_module("amp_admin_server", "admin").app


def disabled_mcp_app(service: str):
    """Listen when ENABLE_MCP_* is false. AMP cannot skip start_application tasks."""

    async def health(_request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "disabled",
                "service": service,
                "profile": "amp",
                "reason": "adapter_disabled",
            }
        )

    async def mcp(_request: Request) -> JSONResponse:
        return JSONResponse({"error": "adapter_disabled", "service": service}, status_code=404)

    return Starlette(
        routes=[
            Route("/health", health, methods=["GET", "HEAD"]),
            Route("/healthcheck", health, methods=["GET", "HEAD"]),
            Route("/", health, methods=["GET", "HEAD"]),
            Route("/mcp", mcp, methods=["GET", "HEAD", "POST", "DELETE"]),
            Route("/mcp/{rest:path}", mcp, methods=["GET", "HEAD", "POST", "DELETE"]),
        ]
    )


def startup_error_app(service: str, exc: BaseException):
    """Listen anyway so CML marks the application running; MCP stays fail-closed."""
    detail = f"{type(exc).__name__}: {exc}".replace("\n", " ")[:400]
    print(
        json.dumps(
            {
                "service": service,
                "event": "startup_failed",
                "error": type(exc).__name__,
                "detail": detail,
                "profile": "amp",
            }
        ),
        flush=True,
    )

    async def health(_request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "error",
                "service": service,
                "profile": "amp",
                "reason": "startup_failed",
                "error": type(exc).__name__,
                "detail": detail,
            }
        )

    return Starlette(
        routes=[
            Route("/health", health, methods=["GET", "HEAD"]),
            Route("/healthcheck", health, methods=["GET", "HEAD"]),
            Route("/", health, methods=["GET", "HEAD"]),
        ]
    )


def event_loop_running() -> bool:
    """True inside IPython/CML cells. uvicorn.asyncio_run cannot start a second loop there."""
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def serve_cml_app(app, *, service: str) -> None:
    import threading

    import uvicorn

    port = cml_port()
    host = cml_bind_host()
    print(
        json.dumps(
            {"service": service, "event": "listen", "host": host, "port": port, "profile": "amp"}
        ),
        flush=True,
    )
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    if event_loop_running():
        server.install_signal_handlers = False
        thread = threading.Thread(target=server.run, name=f"{service}-uvicorn", daemon=False)
        thread.start()
        thread.join()
        return
    server.run()
