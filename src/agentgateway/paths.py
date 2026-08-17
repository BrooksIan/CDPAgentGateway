from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    override = os.environ.get("AGENTGATEWAY_ROOT")
    if override:
        return Path(override).resolve()
    starts = [Path.cwd().resolve(), Path(__file__).resolve()]
    seen: set[Path] = set()
    for start in starts:
        for candidate in [start, *start.parents]:
            if candidate in seen:
                continue
            seen.add(candidate)
            if (candidate / "deploy" / "docker-compose.yml").exists() and (
                candidate / "inventory" / "cdp.yaml"
            ).exists():
                return candidate
    raise FileNotFoundError(
        "Cannot find the AgentGateway repo. cd to it or set AGENTGATEWAY_ROOT."
    )
