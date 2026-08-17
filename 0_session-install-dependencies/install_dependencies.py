#!/usr/bin/env python3
"""AMP session: install extras into the project user site so applications can import them."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    # --user writes to /home/cdsw/.local (project filesystem). A bare -e install
    # stays in the job engine and CML applications then fail to import starlette.
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


if __name__ == "__main__":
    raise SystemExit(main())
