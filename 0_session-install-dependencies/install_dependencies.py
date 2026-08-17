#!/usr/bin/env python3
"""AMP job: install the operator package and AMP extras into the CML runtime."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-e", f"{ROOT}[amp,hive]"],
        cwd=ROOT,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
