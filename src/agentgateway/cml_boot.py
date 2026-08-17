"""Stdlib-only CML helpers. Safe to import before Starlette is installed."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def ensure_src_path(root: Path) -> None:
    src = str(root / "src")
    if src in sys.path:
        sys.path.remove(src)
    sys.path.insert(0, src)


def ensure_amp_extra(root: Path, extra: str = "amp") -> None:
    try:
        import starlette  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--user", "-e", f"{root}[{extra}]"],
            cwd=root,
        )
