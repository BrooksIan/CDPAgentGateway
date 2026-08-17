from __future__ import annotations

from agentgateway.env import agent_caller_key
from agentgateway.token import knox_claims, private_key, public_key, sign_rs256, unsigned_token

__all__ = [
    "knox_claims",
    "mcp_headers",
    "private_key",
    "public_key",
    "sign_rs256",
    "unsigned_token",
]


def mcp_headers(token: str) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    key = agent_caller_key()
    if key:
        headers["X-Agent-Key"] = key
    return headers
