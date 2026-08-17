#!/usr/bin/env python3
"""Render APISIX standalone config and copy the Knox verifying key."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentgateway.config import write_apisix_config
from agentgateway.env import load_env


def main() -> None:
    path = write_apisix_config()
    values = load_env()
    print(path)
    print(
        "upstream="
        f"{values['UPSTREAM_SCHEME']}://{values['UPSTREAM_HOST']}:{values['UPSTREAM_PORT']}"
        f"{values['KNOX_PROXY_PREFIX']}"
    )


if __name__ == "__main__":
    main()
