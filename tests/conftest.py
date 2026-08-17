from __future__ import annotations

import os
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
