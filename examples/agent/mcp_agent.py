"""Minimal MCP JSON-RPC client for third-party agent demos (Compose or CML AMP).

Never log or print the Knox bearer. Paste it in the notebook token cell (`getpass`) or set
`KNOX_TOKEN` / `KNOX_TOKEN_FILE` for this process only. Do not commit tokens or put them in
AMP project environment.
"""

from __future__ import annotations

import json
import os
import sys
import time
from getpass import getpass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

_ADAPTERS = {
    "spark": "/mcp/spark",
    "hive": "/mcp/hive",
    "impala": "/mcp/impala",
}
_AMP_SUBDOMAINS = {
    "spark": "mcp-spark",
    "hive": "mcp-hive",
    "impala": "mcp-impala",
}


def _repo_src() -> Path:
    root = Path(os.environ.get("AGENTGATEWAY_ROOT") or Path.cwd()).resolve()
    if not (root / "pyproject.toml").is_file():
        alt = Path("/home/cdsw")
        if (alt / "pyproject.toml").is_file():
            root = alt.resolve()
    return root / "src"


def load_knox_token(*, prompt: bool = True) -> str:
    """Load a Knox JWT for this process only. Never print or persist it to git."""
    token = (os.environ.get("KNOX_TOKEN") or "").strip()
    if token:
        return token
    path = (os.environ.get("KNOX_TOKEN_FILE") or "").strip()
    if path:
        file = Path(path).expanduser()
        if file.is_file():
            token = file.read_text().strip()
            if token:
                os.environ["KNOX_TOKEN"] = token
                return token
    if prompt:
        token = getpass("Knox JWT (not echoed; session only): ").strip()
        if token:
            os.environ["KNOX_TOKEN"] = token
            return token
    raise RuntimeError(
        "No Knox JWT in this session. Run the notebook token cell (getpass) or export "
        "KNOX_TOKEN for this engine only. Do not put the bearer in AMP project env or git."
    )


def _load_token() -> str:
    return load_knox_token(prompt=True)


def profile() -> str:
    if os.environ.get("CDSW_DOMAIN") or os.environ.get("CDSW_PROJECT"):
        return "amp"
    return "compose"


def mcp_base_url(adapter: str = "spark") -> str:
    key = (adapter or "spark").strip().lower()
    if key not in _ADAPTERS:
        raise ValueError(f"unknown adapter {adapter!r}; use spark, hive, or impala")
    env_key = f"MCP_{key.upper()}_URL"
    explicit = (os.environ.get(env_key) or os.environ.get(f"AGENT_{env_key}") or "").strip()
    if explicit:
        return explicit.rstrip("/")
    domain = (os.environ.get("CDSW_DOMAIN") or "").strip()
    if domain:
        public = (os.environ.get("GATEWAY_PUBLIC_URL") or "").strip().rstrip("/")
        if not public:
            public = f"https://agent-gateway.{domain.rstrip('/')}"
        return f"{public}{_ADAPTERS[key]}"
    gateway = (os.environ.get("GATEWAY_URL") or "http://127.0.0.1:9080").rstrip("/")
    return f"{gateway}{_ADAPTERS[key]}"


def agent_headers(*, adapter: str = "spark") -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {_load_token()}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if profile() == "compose":
        caller = (os.environ.get("AGENT_CALLER_KEY") or "lab-agent").strip()
        if caller:
            headers["X-Agent-Key"] = caller
    else:
        caller = (os.environ.get("AGENT_CALLER_KEY") or "").strip()
        if caller:
            headers["X-Agent-Key"] = caller
    return headers


def mcp_request(
    method: str,
    *,
    adapter: str = "spark",
    params: dict[str, Any] | None = None,
    rpc_id: int = 1,
    timeout: float = 60.0,
) -> dict[str, Any]:
    url = mcp_base_url(adapter)
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": rpc_id, "method": method}
    if params is not None:
        payload["params"] = params
    with httpx.Client(timeout=timeout, verify=_tls_verify()) as client:
        response = client.post(url, json=payload, headers=agent_headers(adapter=adapter))
    if response.status_code >= 400:
        snippet = (response.text or "").replace("\n", " ").strip()[:400]
        raise RuntimeError(f"MCP HTTP {response.status_code} from {url}: {snippet}")
    body = response.json()
    if "error" in body:
        raise RuntimeError(json.dumps(body["error"], indent=2))
    return body["result"]


def tools_list(adapter: str = "spark") -> list[dict[str, Any]]:
    result = mcp_request("tools/list", adapter=adapter)
    return list(result.get("tools") or [])


def tools_call(
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    adapter: str = "spark",
    rpc_id: int = 1,
    timeout: float = 60.0,
) -> dict[str, Any]:
    return mcp_request(
        "tools/call",
        adapter=adapter,
        params={"name": name, "arguments": arguments or {}},
        rpc_id=rpc_id,
        timeout=timeout,
    )


def tool_payload(result: dict[str, Any]) -> dict[str, Any]:
    content = result.get("content") or []
    if not content:
        return {}
    text = content[0].get("text") or ""
    return json.loads(text) if text else {}


def require_tool_ok(result: dict[str, Any]) -> dict[str, Any]:
    payload = tool_payload(result)
    if result.get("isError"):
        raise RuntimeError(json.dumps(payload or result, indent=2))
    return payload


_TOOL_PREFIXES = (
    ("spark_", "spark"),
    ("hive_", "hive"),
    ("impala_", "impala"),
)


def adapter_for_tool(name: str) -> str:
    """Map spark_*, hive_*, or impala_* tool names to an MCP adapter."""
    key = (name or "").strip()
    for prefix, adapter in _TOOL_PREFIXES:
        if key.startswith(prefix):
            return adapter
    raise ValueError(f"unknown tool {name!r}; expected spark_*, hive_*, or impala_*")


def list_gateway_tools(
    adapters: tuple[str, ...] | list[str] = ("spark", "hive", "impala"),
) -> list[dict[str, Any]]:
    """tools/list across adapters. Each spec includes adapter. Never logs the bearer."""
    catalog: list[dict[str, Any]] = []
    for adapter in adapters:
        for spec in tools_list(adapter):
            item = dict(spec)
            item["adapter"] = adapter
            catalog.append(item)
    return catalog


def call_gateway_tool(
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    adapter: str | None = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """tools/call on the matching adapter. Returns the tool JSON payload."""
    target = (adapter or "").strip() or adapter_for_tool(name)
    return require_tool_ok(tools_call(name, arguments or {}, adapter=target, timeout=timeout))


def knox_user_from_spark() -> str:
    payload = require_tool_ok(tools_call("spark_list_batches", {}, adapter="spark"))
    user = (payload.get("knox_user") or "").strip()
    if not user:
        raise RuntimeError("spark_list_batches did not return knox_user")
    return user


def spark_job_uri(knox_user: str) -> str:
    explicit = (os.environ.get("SPARK_FILE_URI") or "").strip()
    if explicit:
        return explicit
    user = (knox_user or "analyst").split("@", 1)[0]
    return f"hdfs:///user/{user}/examples/count_to_10.py"


def poll_spark_batch(
    batch_id: int,
    *,
    timeout: float | None = None,
    interval: float | None = None,
) -> dict[str, Any]:
    wait = timeout if timeout is not None else float(os.environ.get("SPARK_POLL_TIMEOUT", "600"))
    step = interval if interval is not None else float(os.environ.get("SPARK_POLL_INTERVAL", "10"))
    terminal = {"success", "dead", "killed", "error"}
    deadline = time.monotonic() + wait
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = require_tool_ok(
            tools_call("spark_get_batch", {"batch_id": int(batch_id)}, adapter="spark", timeout=30.0)
        )
        state = str(last.get("state") or "").lower()
        if state in terminal:
            return last
        time.sleep(max(step, 1.0))
    raise TimeoutError(f"batch {batch_id} still {last.get('state')!r} after {wait}s")


def health(adapter: str = "spark") -> dict[str, Any]:
    base = mcp_base_url(adapter).rsplit("/mcp/", 1)[0]
    health_url = urljoin(base + "/", "health")
    with httpx.Client(timeout=10.0, verify=_tls_verify()) as client:
        response = client.get(health_url)
    response.raise_for_status()
    return response.json()


def _tls_verify() -> bool:
    return os.environ.get("UPSTREAM_TLS_VERIFY", "true").lower() in {"true", "1", "yes"}


def ensure_import_path() -> None:
    src = _repo_src()
    path = str(src)
    if path not in sys.path:
        sys.path.insert(0, path)
