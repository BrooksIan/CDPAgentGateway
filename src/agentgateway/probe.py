from __future__ import annotations

import httpx

from agentgateway.env import gateway_url


def request_path(
    path: str,
    *,
    token: str,
    method: str = "GET",
    params: list[tuple[str, str]] | None = None,
    timeout: float = 15.0,
) -> httpx.Response:
    if not path.startswith("/"):
        path = "/" + path
    url = f"{gateway_url().rstrip('/')}{path}"
    return httpx.request(
        method.upper(),
        url,
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=timeout,
        follow_redirects=False,
    )


def parse_params(items: list[str] | None) -> list[tuple[str, str]]:
    params: list[tuple[str, str]] = []
    for item in items or []:
        if "=" not in item:
            raise ValueError(f"Expected key=value, got {item!r}")
        key, value = item.split("=", 1)
        params.append((key, value))
    return params
