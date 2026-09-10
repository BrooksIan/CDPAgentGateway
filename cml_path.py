"""Repo-root CML bootstrap. Entrypoints import this before agentgateway is on sys.path.

CML may run scripts as IPython with no __file__, and sys.path[0] is the script
directory (e.g. 3_app-mcp-spark/), not the project root. Each entrypoint therefore
keeps a short identical prelude that finds this file, then calls bootstrap() or a
run_* helper so path/install/serve logic stays in one place.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def find_root() -> Path:
    def is_root(path: Path) -> bool:
        return (path / "cml_path.py").is_file() and (path / ".project-metadata.yaml").is_file()

    raw = (os.environ.get("AGENTGATEWAY_ROOT") or "").strip()
    if raw:
        path = Path(raw).expanduser().resolve()
        if is_root(path):
            return path
    cwd = Path.cwd().resolve()
    for candidate in [cwd, *cwd.parents]:
        if is_root(candidate):
            return candidate
    home = Path("/home/cdsw")
    if is_root(home):
        return home.resolve()
    raise FileNotFoundError(
        "Cannot find AgentGateway project root (looked at AGENTGATEWAY_ROOT, cwd, /home/cdsw)"
    )


def bootstrap() -> Path:
    """Put project root and src on sys.path so `agentgateway` imports work."""
    root = find_root()
    root_s = str(root)
    src = str(root / "src")
    for path in (src, root_s):
        if path in sys.path:
            sys.path.remove(path)
    sys.path.insert(0, src)
    sys.path.insert(0, root_s)
    return root


def _should_serve(name: str) -> bool:
    return name == "__main__" or bool(os.environ.get("CDSW_APP_PORT"))


def run_mcp(adapter: str, *, name: str):
    """Build one MCP adapter app; serve when CML starts the application."""
    bootstrap()
    from agentgateway.amp import serve_cml_app
    from agentgateway.cml_boot import mcp_adapter_app

    app, service = mcp_adapter_app(adapter)
    if _should_serve(name):
        serve_cml_app(app, service=service)
    return app


def run_admin(*, name: str):
    """Build the operator admin app; serve when CML starts the application."""
    bootstrap()
    from agentgateway.amp import build_admin_app, serve_cml_app, startup_error_app
    from agentgateway.cml_boot import boot_amp

    boot_amp()
    try:
        app = build_admin_app()
    except Exception as exc:  # noqa: BLE001 — surface startup failure on /health
        app = startup_error_app("admin", exc)
    if _should_serve(name):
        serve_cml_app(app, service="admin")
    return app


def run_agent_gateway(*, name: str) -> None:
    """Start APISIX (or the in-process Python edge) when CML starts the application."""
    bootstrap()
    from agentgateway.amp_apisix import serve_amp_apisix
    from agentgateway.cml_boot import boot_amp, run_amp_main

    boot_amp()
    if _should_serve(name):
        run_amp_main(serve_amp_apisix)
