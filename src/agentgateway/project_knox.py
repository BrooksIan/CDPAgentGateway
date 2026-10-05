"""Fill AMP project Knox URLs from inventory/cdp.yaml at launch.

`.project-metadata.yaml` keeps a non-empty placeholder so the Configure Project
form still loads. A typed Livy URL is left as the operator override.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

import yaml

from agentgateway.cml_boot import in_cml
from agentgateway.paths import repo_root

# Reserved `.invalid` host. Satisfies CML's non-empty required default without
# pinning a cluster. Launch replaces it from inventory/cdp.yaml.
KNOX_PROXY_PLACEHOLDER = "https://knox.invalid/gateway/cdp-proxy-token/livy_for_spark3/"
_PLACEHOLDER_HOSTS = {"knox.invalid"}


def is_unconfigured_knox_url(url: str) -> bool:
    text = (url or "").strip()
    if not text:
        return True
    host = (urlparse(text).hostname or "").lower().rstrip(".")
    return host in _PLACEHOLDER_HOSTS or host.endswith(".invalid")


def knox_urls_from_inventory(root: Path) -> dict[str, str]:
    path = root / "inventory" / "cdp.yaml"
    inventory = yaml.safe_load(path.read_text()) or {}
    knox = inventory.get("knox") or {}
    gateway = str(knox.get("gateway_url") or "").strip().rstrip("/")
    prefix = str(knox.get("proxy_prefix") or "").strip()
    jwks = str(knox.get("jwks_url") or "").strip()
    if not gateway or not prefix:
        raise ValueError("inventory/cdp.yaml needs knox.gateway_url and knox.proxy_prefix")
    if not prefix.startswith("/"):
        prefix = "/" + prefix
    prefix = prefix.rstrip("/")
    if not jwks:
        jwks_path = str(knox.get("jwks_path") or "").strip()
        if not jwks_path:
            raise ValueError("inventory/cdp.yaml needs knox.jwks_url or knox.jwks_path")
        if not jwks_path.startswith("/"):
            jwks_path = "/" + jwks_path
        jwks = f"{gateway}{jwks_path}"
    return {
        "KNOX_PROXY_URL": f"{gateway}{prefix}/livy_for_spark3/",
        "KNOX_JWKS_URL": jwks,
    }


def pending_knox_updates(environ: Mapping[str, str], inventory_urls: Mapping[str, str]) -> dict[str, str]:
    """Return project env keys still on the placeholder. A real Livy URL wins."""
    updates: dict[str, str] = {}
    if not is_unconfigured_knox_url(environ.get("KNOX_PROXY_URL") or ""):
        return updates
    updates["KNOX_PROXY_URL"] = inventory_urls["KNOX_PROXY_URL"]
    if is_unconfigured_knox_url(environ.get("KNOX_JWKS_URL") or ""):
        updates["KNOX_JWKS_URL"] = inventory_urls["KNOX_JWKS_URL"]
    return updates


def apply_inventory_knox(root: Path | None = None) -> dict[str, str]:
    """Point this process at inventory Knox when project env is still the placeholder."""
    root = root or repo_root()
    try:
        inventory_urls = knox_urls_from_inventory(root)
    except (OSError, ValueError, KeyError, yaml.YAMLError):
        return {}
    updates = pending_knox_updates(os.environ, inventory_urls)
    for key, value in updates.items():
        os.environ[key] = value
    return updates


def _project_setting_names(root: Path) -> list[str]:
    meta = yaml.safe_load((root / ".project-metadata.yaml").read_text()) or {}
    return list((meta.get("environment_variables") or {}).keys())


def _as_env_map(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): "" if item is None else str(item) for key, item in value.items()}


def merged_project_env(root: Path, fetched: Any, updates: Mapping[str, str]) -> dict[str, str]:
    """Merge inventory Knox URLs into the project map without dropping other settings."""
    merged = _as_env_map(fetched)
    for name in _project_setting_names(root):
        if name not in merged and os.environ.get(name) is not None:
            merged[name] = os.environ[name]
    merged.update(updates)
    return merged


def _update_body(environment: dict[str, str]) -> Any:
    try:
        import cmlapi
    except ImportError:
        return {"environment": environment}
    return cmlapi.Project(environment=environment)


def publish_project_knox(
    root: Path,
    updates: Mapping[str, str],
    *,
    client: Any = None,
    project_id: str | None = None,
) -> bool:
    if not updates:
        return False
    project_id = (project_id or os.environ.get("CDSW_PROJECT_ID") or "").strip()
    if client is None:
        if not project_id or not in_cml():
            return False
        try:
            import cmlapi
        except ImportError:
            print('{"event":"knox_project_env_skipped","reason":"cmlapi_missing"}', flush=True)
            return False
        client = cmlapi.default_client()
    if not project_id:
        return False
    project = client.get_project(project_id)
    merged = merged_project_env(root, getattr(project, "environment", None), updates)
    client.update_project(_update_body(merged), project_id)
    return True


def configure_project_knox(
    root: Path,
    *,
    client: Any = None,
    project_id: str | None = None,
) -> dict[str, str]:
    """AMP launch: write inventory Knox URLs into this process and project settings."""
    if not is_unconfigured_knox_url(os.environ.get("KNOX_PROXY_URL") or ""):
        return {}
    updates = pending_knox_updates(os.environ, knox_urls_from_inventory(root))
    for key, value in updates.items():
        os.environ[key] = value
    published = False
    if updates:
        try:
            published = publish_project_knox(root, updates, client=client, project_id=project_id)
        except Exception as exc:
            print(
                json.dumps({"event": "knox_project_env_failed", "error": type(exc).__name__}),
                flush=True,
            )
    if updates:
        host = urlparse(updates["KNOX_PROXY_URL"]).hostname or ""
        print(
            json.dumps({"event": "knox_project_env", "proxy_host": host, "published": published}),
            flush=True,
        )
    return updates
