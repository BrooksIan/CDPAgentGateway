"""Stdlib-only CML helpers. Safe to import before Starlette is installed."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def in_cml() -> bool:
    return any(os.environ.get(name) for name in ("CDSW_ENGINE_ID", "CDSW_PROJECT", "CDSW_APP_PORT"))


def in_ipython() -> bool:
    if in_cml():
        return True
    if "IPython" not in sys.modules and "ipykernel" not in sys.modules:
        return False
    try:
        from IPython import get_ipython as _get_ipython

        return _get_ipython() is not None
    except Exception:
        return True


def run_amp_main(main_fn) -> int:
    """Run an AMP job without sys.exit. CML IPython shows SystemExit(0) as a traceback."""
    return int(main_fn())


def require_python() -> None:
    if sys.version_info < (3, 11):
        version = sys.version.split()[0]
        raise RuntimeError(
            f"CDP Agent Gateway requires Python 3.11 or greater; this runtime is {version}"
        )


def project_root() -> Path:
    """Locate the git/project root. CML IPython sessions do not define __file__ in the script."""
    require_python()

    def is_root(path: Path) -> bool:
        return (path / ".project-metadata.yaml").is_file() and (path / "pyproject.toml").is_file()

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
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if is_root(candidate):
            return candidate
    raise FileNotFoundError(
        "Cannot find AgentGateway project root (looked at cwd, /home/cdsw, and package path)"
    )


def ensure_src_path(root: Path | None = None) -> Path:
    root = root or project_root()
    src = str(root / "src")
    if src in sys.path:
        sys.path.remove(src)
    sys.path.insert(0, src)
    return root


def ensure_amp_extra(root: Path, extra: str = "amp") -> None:
    try:
        import starlette  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--user", "-e", f"{root}[{extra}]"],
            cwd=root,
        )


# impyla (the "hive" extra) drives BOTH the Hive and the Impala HS2 adapters. The
# extra name is misleading; renaming it is out of scope for this change.
AMP_EXTRAS: dict[str, str] = {"spark": "amp", "hive": "amp,hive", "impala": "amp,hive"}


def boot_amp(extra: str = "amp") -> Path:
    """Locate the project root, put src on sys.path, install the AMP extras if missing."""
    root = project_root()
    ensure_src_path(root)
    ensure_amp_extra(root, extra=extra)
    return root


def mcp_adapter_app(adapter: str):
    """CML entrypoint for one MCP adapter. Returns (app, service).

    Stays in cml_boot, not amp, because `ensure_amp_extra` is what installs the
    Starlette that `agentgateway.amp` imports at module scope.
    """
    key = (adapter or "").strip().lower()
    if key not in AMP_EXTRAS:
        raise KeyError(f"unknown MCP adapter {adapter!r}")
    boot_amp(AMP_EXTRAS[key])
    from agentgateway.amp import build_adapter_app

    return build_adapter_app(key)
