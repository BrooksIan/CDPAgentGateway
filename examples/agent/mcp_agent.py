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
from urllib.parse import urljoin, urlparse

import httpx

_ADAPTERS = {
    "spark": "/mcp/spark",
    "hive": "/mcp/hive",
    "impala": "/mcp/impala",
}
_AMP_SUBDOMAINS = {
    "spark": "cdp-ag-spark",
    "hive": "cdp-ag-hive",
    "impala": "cdp-ag-impala",
}


class AdapterDisabled(RuntimeError):
    """AMP returned HTTP 404 adapter_disabled (that MCP application is off)."""

    def __init__(self, adapter: str, url: str) -> None:
        self.adapter = adapter
        self.url = url
        super().__init__(f"MCP adapter {adapter!r} is disabled at {url}")


def _adapter_disabled_name(response: httpx.Response, adapter: str) -> str | None:
    if response.status_code != 404:
        return None
    try:
        body = json.loads(response.text or "")
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(body, dict) or body.get("error") != "adapter_disabled":
        return None
    reason = str(body.get("reason") or body.get("service") or adapter).strip()
    if reason.startswith("mcp-"):
        reason = reason[4:]
    return reason or adapter


def normalize_knox_token(raw: str) -> str:
    """Strip quotes, a duplicated Bearer prefix, and paste wrapping. Never log the value."""
    token = (raw or "").strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in {'"', "'"}:
        token = token[1:-1].strip()
    if token[:7].lower() == "bearer ":
        token = token[7:].strip()
    return "".join(token.split())


def jwt_shaped(token: str) -> bool:
    parts = (token or "").split(".")
    return len(parts) == 3 and all(parts[:2])


def knox_token_status(token: str | None = None) -> dict[str, Any]:
    """Unverified JWT facts for the notebook. Never includes the bearer."""
    raw = normalize_knox_token(token if token is not None else (os.environ.get("KNOX_TOKEN") or ""))
    status: dict[str, Any] = {
        "set": bool(raw),
        "jwt_shaped": jwt_shaped(raw),
        "alg": "",
        "iss": "",
        "sub": "",
        "exp_in_s": None,
        "hint": "",
    }
    if not raw:
        status["hint"] = "missing"
        return status
    if not status["jwt_shaped"]:
        status["hint"] = (
            "not a JWT (need three segments, usually starting with eyJ). "
            "Paste a Knox Token API access token, not a passcode, cookie, or 'Bearer ' prefix. "
            "Gateway reason invalid_token means the bearer did not parse as a JWT."
        )
        return status
    try:
        import jwt as pyjwt

        header = pyjwt.get_unverified_header(raw)
        payload = pyjwt.decode(raw, options={"verify_signature": False})
    except Exception:  # noqa: BLE001
        status["jwt_shaped"] = False
        status["hint"] = (
            "not a parseable JWT. Gateway returns 401 invalid_token for this shape. "
            "Get a fresh token from Knox Token Generation / Token API."
        )
        return status
    status["alg"] = str(header.get("alg") or "")
    status["iss"] = str(payload.get("iss") or "")
    status["sub"] = str(payload.get("sub") or "")
    exp = payload.get("exp")
    if exp is not None:
        try:
            status["exp_in_s"] = int(exp) - int(time.time())
        except (TypeError, ValueError):
            status["exp_in_s"] = None
    if status["alg"] and status["alg"] != "RS256":
        status["hint"] = f"alg={status['alg']} (gateway expects RS256)"
    elif status["iss"] and status["iss"] != "KNOXSSO":
        status["hint"] = f"iss={status['iss']!r} (gateway expects KNOXSSO)"
    elif not status["sub"]:
        status["hint"] = "missing sub"
    elif status["exp_in_s"] is not None and status["exp_in_s"] < 0:
        status["hint"] = "expired; mint a new Knox Token API JWT"
    else:
        status["hint"] = "ok"
    return status


def _repo_src() -> Path:
    root = Path(os.environ.get("AGENTGATEWAY_ROOT") or Path.cwd()).resolve()
    if not (root / "pyproject.toml").is_file():
        alt = Path("/home/cdsw")
        if (alt / "pyproject.toml").is_file():
            root = alt.resolve()
    return root / "src"


def load_knox_token(*, prompt: bool = True, ignore_non_jwt_env: bool = False) -> str:
    """Load a Knox JWT for this process only. Never print or persist it to git.

    ignore_non_jwt_env: notebook token cell should pass True so a Knox passcode in
    KNOX_TOKEN does not skip getpass (that paste becomes gateway 401 invalid_token).
    """
    token = normalize_knox_token(os.environ.get("KNOX_TOKEN") or "")
    if token and ignore_non_jwt_env and not jwt_shaped(token):
        token = ""
    if not token:
        path = (os.environ.get("KNOX_TOKEN_FILE") or "").strip()
        if path:
            file = Path(path).expanduser()
            if file.is_file():
                token = normalize_knox_token(file.read_text())
    if token and ignore_non_jwt_env and not jwt_shaped(token):
        token = ""
    if not token and prompt:
        token = normalize_knox_token(getpass("Knox JWT (not echoed; session only): "))
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


def _url_source(adapter: str) -> str:
    key = (adapter or "spark").strip().lower()
    env_key = f"MCP_{key.upper()}_URL"
    if (os.environ.get(env_key) or os.environ.get(f"AGENT_{env_key}") or "").strip():
        return env_key
    if (os.environ.get("CDSW_DOMAIN") or "").strip():
        if (os.environ.get("GATEWAY_PUBLIC_URL") or "").strip():
            return "GATEWAY_PUBLIC_URL"
        return "CDSW_DOMAIN"
    if (os.environ.get("GATEWAY_URL") or "").strip():
        return "GATEWAY_URL"
    return "default"


def _direct_mcp_url(adapter: str) -> str:
    key = (adapter or "spark").strip().lower()
    domain = (os.environ.get("CDSW_DOMAIN") or "").strip().strip("/")
    if key not in _AMP_SUBDOMAINS or not domain:
        return ""
    return f"https://{_AMP_SUBDOMAINS[key]}.{domain}{_ADAPTERS[key]}"


def knox_livy_from_env() -> str:
    """Knox Livy base from this session's project env. Empty when it is still unset."""
    proxy = (os.environ.get("KNOX_PROXY_URL") or "").strip()
    parsed: dict[str, str] = {}
    if proxy and "knox.invalid" not in proxy:
        try:
            from agentgateway.knox import parse_knox_proxy_url

            parsed = parse_knox_proxy_url(proxy)
        except Exception:
            parsed = {}
    scheme = (parsed.get("UPSTREAM_SCHEME") or os.environ.get("UPSTREAM_SCHEME") or "https").rstrip(":/")
    host = (parsed.get("UPSTREAM_HOST") or os.environ.get("UPSTREAM_HOST") or "").strip()
    port = (parsed.get("UPSTREAM_PORT") or os.environ.get("UPSTREAM_PORT") or "").strip()
    prefix = (parsed.get("KNOX_PROXY_PREFIX") or os.environ.get("KNOX_PROXY_PREFIX") or "").rstrip("/")
    if not host or not prefix:
        return ""
    origin = f"{scheme}://{host}"
    if port and not ((scheme == "https" and port == "443") or (scheme == "http" and port == "80")):
        origin = f"{origin}:{port}"
    return f"{origin}{prefix}/livy_for_spark3"


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
            public = f"https://cdp-ag.{domain.rstrip('/')}"
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


def _mcp_http_error(url: str, response: httpx.Response) -> str:
    snippet = (response.text or "").replace("\n", " ").strip()[:400]
    reason = ""
    try:
        body = json.loads(response.text or "")
        if isinstance(body, dict):
            reason = str(body.get("reason") or body.get("error") or "")
    except Exception:  # noqa: BLE001
        reason = ""
    extra = ""
    if response.status_code == 401 and reason == "invalid_token":
        extra = (
            " The bearer did not parse as a JWT. Re-run the token cell with a Knox Token API "
            "JWT (three segments, usually starts with eyJ). Do not paste a passcode, cookie, "
            "or a second 'Bearer ' prefix."
        )
    elif response.status_code == 401 and reason == "expired":
        extra = " The Knox JWT is expired. Mint a new Token API JWT and re-run the token cell."
    elif response.status_code == 401 and reason == "invalid_signature":
        extra = (
            " Signature does not match the pinned Knox JWKS. Use a live Knox Token API JWT, "
            "not a lab --mint token. Confirm the Fetch JWKS AMP job succeeded."
        )
    return f"MCP HTTP {response.status_code} from {url}: {snippet}{extra}"


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
        disabled = _adapter_disabled_name(response, adapter)
        if disabled:
            raise AdapterDisabled(disabled, url)
        raise RuntimeError(_mcp_http_error(url, response))
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
    *,
    skipped: list[str] | None = None,
) -> list[dict[str, Any]]:
    """tools/list across adapters. Skips AMP adapter_disabled (404). Never logs the bearer."""
    catalog: list[dict[str, Any]] = []
    disabled: list[str] = skipped if skipped is not None else []
    for adapter in adapters:
        try:
            specs = tools_list(adapter)
        except AdapterDisabled:
            disabled.append(adapter)
            continue
        for spec in specs:
            item = dict(spec)
            item["adapter"] = adapter
            catalog.append(item)
    if not catalog:
        names = ", ".join(disabled) or "none"
        raise RuntimeError(
            f"No MCP tools listed. Disabled adapters: {names}. "
            "Enable Spark/Hive on agent-gateway (Impala is optional)."
        )
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


_REUSE_BATCH_STATES = frozenset({"not_started", "starting", "running", "recovering", "success"})


def existing_spark_batch(name: str = "count-to-10") -> dict[str, Any] | None:
    """Newest Livy batch with this name that is in-flight or already succeeded."""
    payload = require_tool_ok(tools_call("spark_list_batches", {}, adapter="spark"))
    items = payload.get("items") or []
    named = [item for item in items if isinstance(item, dict) and str(item.get("name") or "") == name]
    named.sort(key=lambda item: int(item["id"]) if str(item.get("id", "")).isdigit() else -1, reverse=True)
    for batch in named:
        if str(batch.get("state") or "").lower() in _REUSE_BATCH_STATES and batch.get("id") is not None:
            return batch
    return None


def submit_spark_example(
    *,
    file_uri: str,
    name: str = "count-to-10",
    force: bool | None = None,
) -> dict[str, Any]:
    """Submit count-to-10, or reuse an in-flight/success batch so a notebook re-run does not double Livy."""
    if force is None:
        force = os.environ.get("SPARK_FORCE_SUBMIT", "").strip().lower() in {"1", "true", "yes"}
    if not force:
        found = existing_spark_batch(name)
        if found is not None:
            return {**found, "reused": True, "submitted": False}
    submit = require_tool_ok(
        tools_call(
            "spark_submit_batch",
            {"file": file_uri, "name": name},
            adapter="spark",
            timeout=120.0,
        )
    )
    return {**submit, "reused": False}


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


def debug_endpoints(*, probe: bool = True) -> dict[str, Any]:
    """URLs this notebook will call, plus what the running application reports.

    Does not include the Knox bearer. A redirect on GET /health is recorded as
    application_redirect (host only).
    """
    info: dict[str, Any] = {
        "profile": profile(),
        "agent_url": mcp_base_url("spark"),
        "agent_url_source": _url_source("spark"),
        "hive_url": mcp_base_url("hive"),
        "impala_url": mcp_base_url("impala"),
        "mcp_url": _direct_mcp_url("spark"),
        "knox_livy_url": knox_livy_from_env(),
    }
    if probe:
        info.update(_probe_application())
    return info


def print_debug_endpoints() -> dict[str, Any]:
    """Print the active agent, direct MCP, and Knox Livy URLs. Safe to re-run."""
    info = debug_endpoints()
    print("profile:", info["profile"])
    print("agent_url:", info["agent_url"], f"({info['agent_url_source']})")
    print("mcp_url:", info["mcp_url"] or "(same as agent_url)")
    print("hive url:", info["hive_url"])
    print("impala url:", info["impala_url"])
    print("knox_livy_url:", info["knox_livy_url"] or "(unset)")
    print("application health:", info.get("application_health") or "(not probed)")
    if info.get("application_redirect"):
        print("application redirect:", info["application_redirect"])
    for key in ("application_agent_url", "application_mcp_url", "application_knox_livy_url"):
        if info.get(key):
            print(f"{key}:", info[key])
    return info


def _probe_application() -> dict[str, Any]:
    base = mcp_base_url("spark").rsplit("/mcp/", 1)[0]
    health_url = urljoin(base + "/", "health")
    try:
        with httpx.Client(timeout=10.0, verify=_tls_verify(), follow_redirects=False) as client:
            response = client.get(health_url)
    except Exception as exc:
        return {"application_health": type(exc).__name__}
    found: dict[str, Any] = {"application_health": str(response.status_code)}
    location = (response.headers.get("location") or "").strip()
    if location:
        found["application_redirect"] = urlparse(location).hostname or location[:160]
    if response.status_code != 200:
        return found
    try:
        body = response.json()
    except Exception:
        return found
    if not isinstance(body, dict):
        return found
    for key in ("agent_url", "mcp_url", "knox_livy_url"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            found[f"application_{key}"] = value.strip()
    mcp_urls = body.get("mcp_urls")
    spark = mcp_urls.get("spark") if isinstance(mcp_urls, dict) else ""
    if isinstance(spark, str) and spark.strip():
        found["application_agent_url"] = spark.strip()
    return found


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
