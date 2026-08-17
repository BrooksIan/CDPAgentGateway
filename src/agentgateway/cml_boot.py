"""Stdlib-only CML helpers. Safe to import before Starlette is installed."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


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
