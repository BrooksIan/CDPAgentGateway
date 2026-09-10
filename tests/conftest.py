from __future__ import annotations

import os
import sys
import time

import httpx
import pytest

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://127.0.0.1:9080")


def wait_for_gateway(url: str, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            response = httpx.get(f"{url}/health", timeout=2.0)
            if response.status_code == 200:
                return
            last_error = f"status {response.status_code}"
        except httpx.HTTPError as exc:
            last_error = str(exc)
        time.sleep(1)
    raise RuntimeError(f"APISIX gateway not ready at {url}: {last_error}")


@pytest.fixture(scope="session")
def gateway_url() -> str:
    wait_for_gateway(GATEWAY_URL)
    return GATEWAY_URL.rstrip("/")


@pytest.fixture(scope="session")
def client(gateway_url: str) -> httpx.Client:
    with httpx.Client(base_url=gateway_url, timeout=10.0) as session:
        yield session


@pytest.fixture(autouse=True)
def _restore_process_env():
    """AMP startup helpers mutate the process globally.

    `agentgateway.amp.apply_live_upstream()` calls `os.environ.setdefault(...)` for
    GATEWAY_MODE, ADMIN_BACKEND, ADMIN_DB and the parsed Knox values, and appends
    `<root>/admin` to sys.path. That is correct in an AMP process (once, at startup)
    but it outlives the test that triggered it, and monkeypatch cannot undo a
    setdefault it did not make. Snapshot and restore instead.
    """
    saved_env = dict(os.environ)
    saved_path = list(sys.path)
    yield
    for key in [key for key in os.environ if key not in saved_env]:
        del os.environ[key]
    for key, value in saved_env.items():
        if os.environ.get(key) != value:
            os.environ[key] = value
    sys.path[:] = saved_path
