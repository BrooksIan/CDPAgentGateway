from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence

from agentgateway.paths import repo_root


def compose_argv(*args: str) -> list[str]:
    root = repo_root()
    compose_file = root / "deploy" / "docker-compose.yml"
    docker = shutil.which("docker")
    if docker:
        return [
            docker,
            "compose",
            "--project-directory",
            str(root),
            "-f",
            str(compose_file),
            *args,
        ]
    legacy = shutil.which("docker-compose")
    if legacy:
        return [legacy, "--project-directory", str(root), "-f", str(compose_file), *args]
    raise FileNotFoundError("docker compose is not installed")


def compose_run(
    args: Sequence[str],
    *,
    check: bool = True,
    capture: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        compose_argv(*args),
        check=check,
        text=True,
        capture_output=capture,
        env=env,
    )
