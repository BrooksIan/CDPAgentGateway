from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass

import yaml

from agentgateway.env import admin_url, gateway_url, load_env
from agentgateway.paths import repo_root


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def run_checks(*, ping: bool = False) -> list[Check]:
    root = repo_root()
    env = load_env()
    checks: list[Check] = []

    checks.append(
        Check(
            "python",
            sys.version_info >= (3, 11),
            f"{sys.version.split()[0]} (need 3.11+)",
        )
    )
    docker = shutil.which("docker")
    checks.append(Check("docker", docker is not None, docker or "not on PATH"))
    compose = docker is not None or shutil.which("docker-compose") is not None
    checks.append(Check("compose", compose, "docker compose" if compose else "missing"))

    dotenv = root / ".env"
    checks.append(Check(".env", dotenv.exists(), str(dotenv if dotenv.exists() else "copy .env.example")))

    mode = env.get("GATEWAY_MODE", "local")
    checks.append(Check("mode", mode in {"local", "live"}, mode))

    if mode == "live":
        live_pem = root / "conf" / "keys" / "knox-live.pem"
        custom = env.get("KNOX_PUBLIC_KEY_FILE")
        pem = root / custom if custom else live_pem
        if custom and not pem.is_absolute():
            pem = root / custom
        checks.append(Check("knox public key", pem.exists(), str(pem)))
        checks.append(
            Check(
                "knox proxy",
                bool(env.get("KNOX_PROXY_URL") or env.get("UPSTREAM_HOST") != "mock-cdp"),
                env.get("KNOX_PROXY_URL") or env.get("UPSTREAM_HOST", "unset"),
            )
        )
        checks.append(
            Check(
                "knox token",
                bool(env.get("KNOX_TOKEN")),
                "set in .env" if env.get("KNOX_TOKEN") else "missing (gateway token set)",
            )
        )
    else:
        pub = root / "conf" / "keys" / "public.pem"
        checks.append(Check("test keys", pub.exists(), str(pub)))

    generated = root / "conf" / "generated" / "apisix.yaml"
    checks.append(Check("apisix.yaml", generated.exists(), str(generated)))

    inventory = yaml.safe_load((root / "inventory" / "cdp.yaml").read_text())
    knox = inventory.get("knox") or {}
    checks.append(
        Check(
            "inventory issuer",
            knox.get("issuer") == "KNOXSSO" and knox.get("expected_alg") == "RS256",
            f"iss={knox.get('issuer')} alg={knox.get('expected_alg')}",
        )
    )

    if ping:
        try:
            import httpx

            url = f"{gateway_url(env).rstrip('/')}/health"
            response = httpx.get(url, timeout=2.0)
            checks.append(Check("health", response.status_code == 200, f"{url} -> {response.status_code}"))
            mcp = httpx.get(f"{gateway_url(env).rstrip('/')}/mcp/spark", timeout=2.0)
            checks.append(
                Check(
                    "mcp spark",
                    mcp.status_code == 401,
                    f"/mcp/spark -> {mcp.status_code} (expect 401 without token)",
                )
            )
            admin = httpx.get(f"{admin_url(env).rstrip('/')}/health", timeout=2.0)
            checks.append(
                Check(
                    "admin ui",
                    admin.status_code == 200,
                    f"{admin_url(env)} -> {admin.status_code}",
                )
            )
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("health", False, str(exc)))

    return checks
