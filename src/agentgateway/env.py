from __future__ import annotations

import os
from pathlib import Path

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

    rendered = template.replace("{{TLS_BLOCK}}", tls_block)
    for key in REQUIRED_RENDER_KEYS:
        rendered = rendered.replace("{{" + key + "}}", merged[key])
    if "{{" in rendered:
        raise ValueError("Unrendered template placeholders remain")
    return rendered
