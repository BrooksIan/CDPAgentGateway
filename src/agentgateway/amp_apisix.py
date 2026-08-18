"""Run the AMP agent edge: Docker APISIX when available, otherwise a Python proxy.

Compose still uses deploy/docker-compose.yml. CML engines often have no Docker, so this
module pins Knox JWKS, then either runs apache/apisix or the same allowlisted routes in Python.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import IO
from urllib.parse import urlparse

import httpx

from agentgateway.amp import (
    BurstLimiter,
    KnoxJWTMiddleware,
    _int_env,
    _load_module,
    _prm_routes,
    amp_public_key_path,
    apply_live_upstream,
    cml_port,
    serve_cml_app,
    startup_error_app,
)
from agentgateway.env import agent_caller_key, load_env, render_apisix_yaml
from agentgateway.keys import fetch_pinned_knox_pubkey
from agentgateway.paths import repo_root

APISIX_IMAGE = os.environ.get("APISIX_IMAGE", "apache/apisix:3.16.0-debian")
_MCP_SERVICES = ("SPARK", "HIVE", "IMPALA")


def build_amp_apisix_env() -> dict[str, str]:
    merged = load_env()
    apply_live_upstream()
    merged.update({key: value for key, value in os.environ.items() if value is not None})
    domain = (merged.get("CDSW_DOMAIN") or os.environ.get("CDSW_DOMAIN") or "").strip()
    if not domain:
        raise ValueError("AMP APISIX requires CDSW_DOMAIN (Cloudera AI Workbench application host)")
    merged["GATEWAY_MODE"] = "live"
    merged.setdefault("GATEWAY_PUBLIC_URL", f"https://agent-gateway.{domain.rstrip('/')}")
    if not (os.environ.get("AGENT_CALLER_KEY") or "").strip():
        merged["AGENT_CALLER_KEY"] = ""
    for svc in _MCP_SERVICES:
        sub = f"mcp-{svc.lower()}"
        merged.setdefault(f"MCP_{svc}_UPSTREAM_SCHEME", "https")
        merged.setdefault(f"MCP_{svc}_UPSTREAM_HOST", f"{sub}.{domain.rstrip('/')}")
        merged.setdefault(f"MCP_{svc}_UPSTREAM_PORT", "443")
        merged.setdefault(f"MCP_{svc}_PASS_HOST", "rewrite")
    return merged


def ensure_amp_knox_pem(root: Path | None = None) -> Path:
    """Fetch the pinned Knox PEM when the JWKS job did not write it yet."""
    root = root or repo_root()
    generated = root / "conf" / "generated" / "knox-public.pem"
    if generated.is_file() and generated.read_text().strip():
        return generated
    apply_live_upstream()
    proxy = (os.environ.get("KNOX_PROXY_URL") or "").strip()
    if not proxy:
        raise FileNotFoundError(
            "Missing conf/generated/knox-public.pem and KNOX_PROXY_URL. "
            "Set KNOX_PROXY_URL on the AMP form, then restart Agent gateway."
        )
    insecure = os.environ.get("UPSTREAM_TLS_VERIFY", "true").lower() not in {"true", "1", "yes"}
    generated.parent.mkdir(parents=True, exist_ok=True)
    out = fetch_pinned_knox_pubkey(
        knox_proxy_url=proxy,
        jwks_url=os.environ.get("KNOX_JWKS_URL"),
        out=generated,
        insecure=insecure,
    )
    live = root / "conf" / "keys" / "knox-live.pem"
    live.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(out, live)
    print(json.dumps({"service": "agent-gateway", "event": "jwks_pinned", "pem": str(out)}), flush=True)
    return out


def write_amp_apisix_config(root: Path | None = None) -> Path:
    root = root or repo_root()
    ensure_amp_knox_pem(root)
    values = build_amp_apisix_env()
    template = (root / "conf" / "apisix.yaml.tpl").read_text()
    rendered = render_apisix_yaml(template, values)
    out_dir = root / "conf" / "generated"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "apisix.yaml"
    out.write_text(rendered)
    if not out.read_text().rstrip().endswith("#END"):
        raise ValueError("apisix.yaml must end with #END")
    return out


def _docker_bin() -> str:
    path = shutil.which("docker")
    if not path:
        raise RuntimeError(
            "docker is not on PATH. AMP APISIX runs the same apache/apisix image as Compose."
        )
    return path


def apisix_container_name(port: int) -> str:
    return f"agentgateway-amp-apisix-{port}"


def launch_apisix_container(root: Path, host_port: int) -> subprocess.Popen[str]:
    docker = _docker_bin()
    name = apisix_container_name(host_port)
    subprocess.run([docker, "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    pem = root / "conf" / "generated" / "knox-public.pem"
    cmd = [
        docker,
        "run",
        "--rm",
        "--name",
        name,
        "-p",
        f"127.0.0.1:{host_port}:9080",
        "-v",
        f"{root / 'conf' / 'config.yaml'}:/usr/local/apisix/conf/config.yaml:ro",
        "-v",
        f"{root / 'conf' / 'generated' / 'apisix.yaml'}:/usr/local/apisix/conf/apisix.yaml:ro",
        "-v",
        f"{pem}:/usr/local/apisix/conf/knox-public.pem:ro",
        "-v",
        f"{root / 'plugins' / 'knox-jwt.lua'}:/opt/custom/apisix/plugins/knox-jwt.lua:ro",
        "-e",
        "APISIX_STAND_ALONE=true",
        APISIX_IMAGE,
    ]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def _stream_process_output(stream: IO[str] | None) -> None:
    if stream is None:
        return
    for line in stream:
        print(line, end="", flush=True)


def _terminate_process(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def run_apisix_process() -> int:
    root = repo_root()
    write_amp_apisix_config(root)
    port = cml_port()
    proc = launch_apisix_container(root, port)
    print(
        json.dumps(
            {
                "service": "agent-gateway",
                "event": "listen",
                "host": "127.0.0.1",
                "port": port,
                "profile": "amp",
                "image": APISIX_IMAGE,
            }
        ),
        flush=True,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            _stream_process_output(proc.stdout)
            raise RuntimeError(f"APISIX container exited early with code {proc.returncode}")
        try:
            probe = httpx.get(f"http://127.0.0.1:{port}/health", timeout=2.0)
            if probe.status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    else:
        _terminate_process(proc)
        raise RuntimeError(f"APISIX did not become healthy on 127.0.0.1:{port}/health within 30s")

    try:
        while proc.poll() is None:
            _stream_process_output(proc.stdout)
            time.sleep(0.5)
        _stream_process_output(proc.stdout)
        return proc.returncode or 0
    except KeyboardInterrupt:
        _terminate_process(proc)
        return 0


def _tls_verify() -> bool:
    default = "false" if os.environ.get("CDSW_APP_PORT") else "true"
    return os.environ.get("UPSTREAM_TLS_VERIFY", default).lower() in {"true", "1", "yes"}


def _knox_origin() -> str:
    scheme = os.environ.get("UPSTREAM_SCHEME") or "https"
    host = os.environ.get("UPSTREAM_HOST") or ""
    port = os.environ.get("UPSTREAM_PORT") or "443"
    if not host:
        raise ValueError("UPSTREAM_HOST is empty; set KNOX_PROXY_URL")
    if (scheme == "https" and port in {"443", ""}) or (scheme == "http" and port in {"80", ""}):
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


_HOP_BY_HOP = {
    "host",
    "content-length",
    "content-encoding",
    "connection",
    "keep-alive",
    "transfer-encoding",
    "te",
    "trailer",
    "trailers",
    "upgrade",
    "proxy-authenticate",
    "proxy-authorization",
}


def _forward_headers(request, url: str) -> dict[str, str]:
    headers = {
        key: value for key, value in request.headers.items() if key.lower() not in _HOP_BY_HOP
    }
    host = urlparse(url).netloc
    if host:
        headers["Host"] = host
    return headers


def _outbound_headers(response: httpx.Response) -> dict[str, str]:
    return {
        key: value for key, value in response.headers.items() if key.lower() not in _HOP_BY_HOP
    }


_MCP_ADAPTER_DIRS = {
    "spark": ("amp_edge_spark", "mcp-spark"),
    "hive": ("amp_edge_hive", "mcp-hive"),
    "impala": ("amp_edge_impala", "mcp-impala"),
}


def load_python_edge_mcp_endpoints() -> dict:
    """Load MCP adapter endpoints in-process. CML cannot hairpin to sibling app HTTPS."""
    endpoints: dict = {}
    for adapter, (module_name, directory) in _MCP_ADAPTER_DIRS.items():
        try:
            endpoints[adapter] = _load_module(module_name, directory).mcp_endpoint
        except Exception as extra:  # noqa: BLE001 — hive extra is optional on AMP
            print(
                json.dumps(
                    {
                        "service": "agent-gateway",
                        "event": "mcp_load_failed",
                        "adapter": adapter,
                        "error": type(extra).__name__,
                        "detail": str(extra)[:200],
                    }
                ),
                flush=True,
            )
    return endpoints


def _mcp_upstream_base(adapter: str, values: dict[str, str]) -> str:
    svc = adapter.upper()
    scheme = values[f"MCP_{svc}_UPSTREAM_SCHEME"]
    host = values[f"MCP_{svc}_UPSTREAM_HOST"]
    port = values[f"MCP_{svc}_UPSTREAM_PORT"]
    if (scheme == "https" and port in {"443", ""}) or (scheme == "http" and port in {"80", ""}):
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def build_python_edge_app():
    """Same allowlisted routes as Compose APISIX when Docker is not on the CML engine."""
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Route

    values = build_amp_apisix_env()
    prefix = (values.get("KNOX_PROXY_PREFIX") or "").rstrip("/")
    knox = _knox_origin()
    caller = agent_caller_key(values)
    endpoints = load_python_edge_mcp_endpoints()

    async def health(_request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "service": "agent-gateway",
                "profile": "amp",
                "engine": "python",
                "mcp": "inprocess",
                "adapters": sorted(endpoints),
            }
        )

    async def _proxy(request: Request, url: str) -> Response:
        body = await request.body()
        try:
            response = httpx.request(
                request.method,
                url,
                headers=_forward_headers(request, url),
                content=body or None,
                params=request.query_params,
                timeout=120.0,
                verify=_tls_verify(),
                follow_redirects=False,
            )
        except httpx.HTTPError as extra:
            return JSONResponse(
                {"error": "upstream_unreachable", "reason": type(extra).__name__},
                status_code=502,
            )
        except Exception as extra:  # noqa: BLE001
            return JSONResponse(
                {"error": "upstream_error", "reason": type(extra).__name__},
                status_code=502,
            )
        try:
            return Response(
                content=response.content,
                status_code=response.status_code,
                headers=_outbound_headers(response),
            )
        except Exception as extra:  # noqa: BLE001 — never leak hop-by-hop 500s to the MCP host
            return JSONResponse(
                {"error": "upstream_error", "reason": type(extra).__name__},
                status_code=502,
            )

    async def mcp_proxy(request: Request) -> Response:
        path = request.url.path
        if caller and request.headers.get("x-agent-key") != caller:
            return JSONResponse({"error": "unauthorized", "reason": "missing_caller_key"}, status_code=401)
        adapter = None
        for name in ("spark", "hive", "impala"):
            marker = f"/mcp/{name}"
            if path == marker or path.startswith(marker + "/"):
                adapter = name
                break
        if adapter is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        handler = endpoints.get(adapter)
        if handler is None:
            return JSONResponse({"error": "adapter_unavailable", "reason": adapter}, status_code=503)
        try:
            return await handler(request)
        except Exception as extra:  # noqa: BLE001 — return JSON, not uvicorn's plain 500
            print(
                json.dumps(
                    {
                        "service": "agent-gateway",
                        "event": "mcp_dispatch_failed",
                        "adapter": adapter,
                        "error": type(extra).__name__,
                    }
                ),
                flush=True,
            )
            return JSONResponse(
                {"error": "mcp_adapter_failed", "reason": type(extra).__name__},
                status_code=502,
            )

    async def cdp_proxy(request: Request) -> Response:
        path = request.url.path
        method = request.method.upper()
        if path.startswith("/cdp/livy_for_spark3"):
            if method not in {"GET", "HEAD"}:
                return JSONResponse({"error": "not_found"}, status_code=404)
        elif path.startswith("/cdp/webhdfs"):
            if method not in {"GET", "HEAD", "PUT"}:
                return JSONResponse({"error": "not_found"}, status_code=404)
        else:
            return JSONResponse({"error": "not_found"}, status_code=404)
        rel = path[len("/cdp") :]
        return await _proxy(request, f"{knox}{prefix}{rel}")

    app = Starlette(
        routes=[
            *_prm_routes(),
            Route("/health", health, methods=["GET", "HEAD"]),
            Route("/healthcheck", health, methods=["GET", "HEAD"]),
            Route("/", health, methods=["GET", "HEAD"]),
            Route("/mcp/spark", mcp_proxy, methods=["GET", "HEAD", "POST", "DELETE"]),
            Route("/mcp/spark/{rest:path}", mcp_proxy, methods=["GET", "HEAD", "POST", "DELETE"]),
            Route("/mcp/hive", mcp_proxy, methods=["GET", "HEAD", "POST", "DELETE"]),
            Route("/mcp/hive/{rest:path}", mcp_proxy, methods=["GET", "HEAD", "POST", "DELETE"]),
            Route("/mcp/impala", mcp_proxy, methods=["GET", "HEAD", "POST", "DELETE"]),
            Route("/mcp/impala/{rest:path}", mcp_proxy, methods=["GET", "HEAD", "POST", "DELETE"]),
            Route("/cdp/{rest:path}", cdp_proxy, methods=["GET", "HEAD", "PUT", "POST", "DELETE"]),
        ]
    )
    limiter = BurstLimiter(_int_env("MCP_RATE_COUNT", 60), _int_env("MCP_RATE_WINDOW", 60))
    return KnoxJWTMiddleware(app, public_key_file=amp_public_key_path(), limiter=limiter)


def serve_amp_apisix() -> int:
    try:
        ensure_amp_knox_pem()
        if shutil.which("docker"):
            try:
                return run_apisix_process()
            except Exception as extra:
                print(
                    json.dumps(
                        {
                            "service": "agent-gateway",
                            "event": "docker_apisix_failed",
                            "error": type(extra).__name__,
                            "detail": str(extra)[:300],
                        }
                    ),
                    flush=True,
                )
        else:
            print(
                json.dumps({"service": "agent-gateway", "event": "python_edge", "reason": "docker_not_on_path"}),
                flush=True,
            )
        serve_cml_app(build_python_edge_app(), service="agent-gateway")
        return 0
    except Exception as extra:
        serve_cml_app(startup_error_app("agent-gateway", extra), service="agent-gateway")
        return 1
