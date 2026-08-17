#!/usr/bin/env python3
"""AMP session: install extras into the project user site so applications can import them.

CML run_session executes this in IPython; do not hide work behind __main__.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    # --user writes to /home/cdsw/.local (project filesystem). A bare -e install
    # stays in the session engine and CML applications then fail to import starlette.
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--user", "-e", f"{ROOT}[amp]"],
        cwd=ROOT,
    )
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--user", "-e", f"{ROOT}[hive]"],
            cwd=ROOT,
        )
    except subprocess.CalledProcessError as exc:
        print(
            f"warning: hive extra failed (exit {exc.returncode}); Spark and admin still start",
            file=sys.stderr,
        )
    return 0


raise SystemExit(main())
