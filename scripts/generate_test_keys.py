#!/usr/bin/env python3
"""Create an RSA keypair that mimics Knox RS256 JWTs for local tests."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentgateway.keys import generate_test_keys


def main() -> None:
    directory = generate_test_keys()
    print(f"keys {directory}")


if __name__ == "__main__":
    main()
