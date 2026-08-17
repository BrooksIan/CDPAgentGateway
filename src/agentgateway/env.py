from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dotenv import dotenv_values

from agentgateway.paths import repo_root

REQUIRED_RENDER_KEYS = [
    "UPSTREAM_SCHEME",
    "UPSTREAM_HOST",
    "UPSTREAM_PORT",
    "UPSTREAM_TLS_VERIFY",
    "KNOX_ISSUER",
    "KNOX_EXPECTED_ALG",
    "KNOX_CLOCK_SKEW",
    "KNOX_PROXY_PREFIX",
    "MCP_RATE_COUNT",
    "MCP_RATE_WINDOW",
]


def load_env() -> dict[str, str]:
    root = repo_root()
    values = {**dotenv_values(root / ".env.example"), **dotenv_values(root / ".env")}
    values.update({key: value for key, value in os.environ.items() if value is not None})
    return {key: str(value) for key, value in values.items() if value is not None}


def lab_test_env(existing: dict[str, str] | None = None) -> dict[str, str]:
    """Mock-CDP overlay for `gateway test`. Does not write `.env`."""
    from agentgateway.knox import LOCAL_UPSTREAM

    merged = dict(existing or load_env())
    merged.update(LOCAL_UPSTREAM)
    merged["KNOX_TOKEN_STATE_URL"] = ""
    return merged


def ensure_dotenv() -> Path:
    root = repo_root()
    dest = root / ".env"
    if not dest.exists():
        dest.write_text((root / ".env.example").read_text())
    return dest


def upsert_dotenv(updates: dict[str, str]) -> Path:
    path = ensure_dotenv()
    original = path.read_text()
    newline = "\n" if original.endswith("\n") or not original else ""
    lines = original.splitlines()
    seen: set[str] = set()
    rewritten: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            rewritten.append(line)
            continue
        key = stripped.split("=", 1)[0]
        if key in updates:
            rewritten.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            rewritten.append(line)
    missing = [key for key in updates if key not in seen]
    if missing:
        rewritten.append("")
        rewritten.append("# set by gateway knox")
        rewritten.extend(f"{key}={updates[key]}" for key in missing)
    text = "\n".join(rewritten)
    if newline or missing:
        text = text.rstrip("\n") + "\n"
    path.write_text(text)
    return path


def gateway_url(values: dict[str, str] | None = None) -> str:
    env = values or load_env()
    return env.get("GATEWAY_URL") or f"http://127.0.0.1:{env.get('APISIX_PORT', '9080')}"


def admin_url(values: dict[str, str] | None = None) -> str:
    env = values or load_env()
    return env.get("ADMIN_URL") or f"http://127.0.0.1:{env.get('ADMIN_PORT', '9090')}"


def public_gateway_url(values: dict[str, str] | None = None) -> str:
    env = values or load_env()
    configured = (env.get("GATEWAY_PUBLIC_URL") or env.get("GATEWAY_URL") or "").strip()
    if configured:
        return configured.rstrip("/")
    return gateway_url(env).rstrip("/")


def resource_metadata_url(values: dict[str, str] | None = None) -> str:
    env = values or load_env()
    configured = (env.get("RESOURCE_METADATA_URL") or "").strip()
    if configured:
        return configured
    return f"{public_gateway_url(env)}/.well-known/oauth-protected-resource"


def oauth_prm_document(values: dict[str, str] | None = None) -> dict[str, Any]:
    env = values or load_env()
    resource = public_gateway_url(env)
    servers: list[str] = []
    raw = (env.get("KNOX_AUTHORIZATION_SERVER") or "").strip()
    if raw:
        servers = [raw]
    return {
        "resource": resource,
        "authorization_servers": servers,
        "bearer_methods_supported": ["header"],
        "resource_signing_alg_values_supported": ["RS256"],
        "scopes_supported": ["cdp"],
    }


def agent_caller_key(values: dict[str, str] | None = None) -> str:
    env = values or load_env()
    return (env.get("AGENT_CALLER_KEY") or "").strip()


def agent_headers(token: str, *, path: str = "") -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if path.startswith("/mcp"):
        key = agent_caller_key()
        if key:
            headers["X-Agent-Key"] = key
    return headers


def token_state_url(values: dict[str, str] | None = None) -> str:
    env = values or load_env()
    explicit = (env.get("KNOX_TOKEN_STATE_URL") or "").strip()
    if explicit:
        return explicit.rstrip("/")
    mode = env.get("GATEWAY_MODE") or "local"
    host = env.get("UPSTREAM_HOST") or "mock-cdp"
    if mode == "local" and host == "mock-cdp":
        return "http://mock-cdp:8080/gateway/homepage/knoxtoken/api/v2/token/state"
    return ""


def _consumers_block(key: str) -> str:
    if not key:
        return ""
    quoted = json.dumps(key)
    return (
        "consumers:\n"
        "  - username: agent-platform\n"
        "    plugins:\n"
        "      key-auth:\n"
        f"        key: {quoted}\n"
        "\n"
    )


def _mcp_key_auth_block(key: str) -> str:
    if not key:
        return ""
    return (
        "      key-auth:\n"
        "        header: X-Agent-Key\n"
        "        hide_credentials: true\n"
    )


def _positive_int(values: dict[str, str], key: str, default: str) -> str:
    raw = values.get(key) or default
    try:
        number = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be an integer") from exc
    if number < 1:
        raise ValueError(f"{key} must be >= 1")
    return str(number)


def render_apisix_yaml(template: str, values: dict[str, str]) -> str:
    merged = dict(values)
    merged["MCP_RATE_COUNT"] = _positive_int(merged, "MCP_RATE_COUNT", "60")
    merged["MCP_RATE_WINDOW"] = _positive_int(merged, "MCP_RATE_WINDOW", "60")
    missing = [key for key in REQUIRED_RENDER_KEYS if not merged.get(key)]
    if missing:
        raise ValueError(f"Missing config values: {', '.join(missing)}")

    if merged["UPSTREAM_SCHEME"] == "https":
        tls_block = f"    tls:\n      verify: {merged['UPSTREAM_TLS_VERIFY'].lower()}\n"
    else:
        tls_block = ""

    caller_key = agent_caller_key(merged)
    state_url = token_state_url(merged)
    if state_url:
        parsed = urlparse(state_url)
        if parsed.hostname and parsed.hostname != merged["UPSTREAM_HOST"]:
            raise ValueError("KNOX_TOKEN_STATE_URL host must match UPSTREAM_HOST")
    prm = json.dumps(oauth_prm_document(merged), separators=(",", ":"))
    extras = {
        "TLS_BLOCK": tls_block,
        "CONSUMERS_BLOCK": _consumers_block(caller_key),
        "MCP_KEY_AUTH_BLOCK": _mcp_key_auth_block(caller_key),
        "RESOURCE_METADATA_URL": resource_metadata_url(merged),
        "KNOX_TOKEN_STATE_URL": state_url,
        "OAUTH_PRM_JSON": prm,
    }
    rendered = template
    for key, value in extras.items():
        rendered = rendered.replace("{{" + key + "}}", value)
    for key in REQUIRED_RENDER_KEYS:
        rendered = rendered.replace("{{" + key + "}}", merged[key])
    if "{{" in rendered:
        raise ValueError("Unrendered template placeholders remain")
    return rendered
