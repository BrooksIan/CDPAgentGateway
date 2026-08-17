"""Run Apache APISIX on Cloudera AI Workbench as the AMP agent edge.

Compose uses the apisix Docker service from deploy/docker-compose.yml. AMP runs the
same image bound to CDSW_APP_PORT and renders upstreams to the sibling MCP apps.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import IO

import httpx

from agentgateway.amp import apply_live_upstream, cml_port, serve_cml_app, startup_error_app
from agentgateway.env import load_env, render_apisix_yaml
from agentgateway.paths import repo_root

APISIX_IMAGE = os.environ.get("APISIX_IMAGE", "apache/apisix:3.16.0-debian")
_MCP_SERVICES = ("SPARK", "HIVE", "IMPALA")


def build_amp_apisix_env() -> dict[str, str]:
    merged = load_env()
    apply_live_upstream()
    merged.update({key: value for key, value in os.environ.items() if value is not None})
    domain = (merged.get("CDSW_DOMAIN") or os.environ.get("CDSW_DOMAIN") or "").strip()
    if not domain:
        raise ValueError("AMP APISIX requires CDSW_DOMAIN (Cloudera AI Workbench application host)")
    merged["GATEWAY_MODE"] = "live"
    merged.setdefault("GATEWAY_PUBLIC_URL", f"https://agent-gateway.{domain.rstrip('/')}")
    for svc in _MCP_SERVICES:
        sub = f"mcp-{svc.lower()}"
        merged.setdefault(f"MCP_{svc}_UPSTREAM_SCHEME", "https")
        merged.setdefault(f"MCP_{svc}_UPSTREAM_HOST", f"{sub}.{domain.rstrip('/')}")
        merged.setdefault(f"MCP_{svc}_UPSTREAM_PORT", "443")
        merged.setdefault(f"MCP_{svc}_PASS_HOST", "rewrite")
    return merged


def write_amp_apisix_config(root: Path | None = None) -> Path:
    root = root or repo_root()
    pem = root / "conf" / "generated" / "knox-public.pem"
    if not pem.is_file():
        raise FileNotFoundError(
            "Missing conf/generated/knox-public.pem. Run the Fetch JWKS AMP job before Agent gateway (APISIX)."
        )
    values = build_amp_apisix_env()
    template = (root / "conf" / "apisix.yaml.tpl").read_text()
    rendered = render_apisix_yaml(template, values)
    out_dir = root / "conf" / "generated"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "apisix.yaml"
    out.write_text(rendered)
    if not out.read_text().rstrip().endswith("#END"):
        raise ValueError("apisix.yaml must end with #END")
    return out


def _docker_bin() -> str:
    path = shutil.which("docker")
    if not path:
        raise RuntimeError(
            "docker is not on PATH. AMP APISIX runs the same apache/apisix image as Compose."
        )
    return path


def apisix_container_name(port: int) -> str:
    return f"agentgateway-amp-apisix-{port}"


def launch_apisix_container(root: Path, host_port: int) -> subprocess.Popen[str]:
    docker = _docker_bin()
    name = apisix_container_name(host_port)
    subprocess.run([docker, "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    pem = root / "conf" / "generated" / "knox-public.pem"
    cmd = [
        docker,
        "run",
        "--rm",
        "--name",
        name,
        "-p",
        f"127.0.0.1:{host_port}:9080",
        "-v",
        f"{root / 'conf' / 'config.yaml'}:/usr/local/apisix/conf/config.yaml:ro",
        "-v",
        f"{root / 'conf' / 'generated' / 'apisix.yaml'}:/usr/local/apisix/conf/apisix.yaml:ro",
        "-v",
        f"{pem}:/usr/local/apisix/conf/knox-public.pem:ro",
        "-v",
        f"{root / 'plugins' / 'knox-jwt.lua'}:/opt/custom/apisix/plugins/knox-jwt.lua:ro",
        "-e",
        "APISIX_STAND_ALONE=true",
        APISIX_IMAGE,
    ]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def _stream_process_output(stream: IO[str] | None) -> None:
    if stream is None:
        return
    for line in stream:
        print(line, end="", flush=True)


def _terminate_process(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def run_apisix_process() -> int:
    root = repo_root()
    write_amp_apisix_config(root)
    port = cml_port()
    proc = launch_apisix_container(root, port)
    print(
        json.dumps(
            {
                "service": "agent-gateway",
                "event": "listen",
                "host": "127.0.0.1",
                "port": port,
                "profile": "amp",
                "image": APISIX_IMAGE,
            }
        ),
        flush=True,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            _stream_process_output(proc.stdout)
            raise RuntimeError(f"APISIX container exited early with code {proc.returncode}")
        try:
            probe = httpx.get(f"http://127.0.0.1:{port}/health", timeout=2.0)
            if probe.status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    else:
        _terminate_process(proc)
        raise RuntimeError(f"APISIX did not become healthy on 127.0.0.1:{port}/health within 30s")

    try:
        while proc.poll() is None:
            _stream_process_output(proc.stdout)
            time.sleep(0.5)
        _stream_process_output(proc.stdout)
        return proc.returncode or 0
    except KeyboardInterrupt:
        _terminate_process(proc)
        return 0


def serve_amp_apisix() -> int:
    try:
        return run_apisix_process()
    except Exception as exc:
        serve_cml_app(startup_error_app("agent-gateway", exc), service="agent-gateway")
        return 1
