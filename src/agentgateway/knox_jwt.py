"""Fail-closed Knox RS256 JWT checks. Same reasons as plugins/knox-jwt.lua.

Never log the raw bearer. Do not follow token jku URLs; callers pin JWKS separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
import jwt

DEFAULT_ISSUER = "KNOXSSO"
DEFAULT_ALG = "RS256"
DEFAULT_CLOCK_SKEW = 60
DEFAULT_REALM = "knox"


class KnoxJWTError(Exception):
    def __init__(self, reason: str, *, status: int = 401):
        super().__init__(reason)
        self.reason = reason
        self.status = status


@dataclass(frozen=True)
class KnoxIdentity:
    sub: str
    token_id: str | None
    payload: dict[str, Any]


def extract_bearer(authorization: str | None) -> str:
    header = (authorization or "").strip()
    if not header:
        raise KnoxJWTError("missing_token")
    prefix = header[:7]
    if prefix not in {"Bearer ", "bearer "}:
        raise KnoxJWTError("missing_token")
    token = header[7:].strip()
    if not token:
        raise KnoxJWTError("missing_token")
    return token


def verify_knox_jwt(
    token: str,
    *,
    public_key_pem: str,
    issuer: str = DEFAULT_ISSUER,
    expected_alg: str = DEFAULT_ALG,
    clock_skew: int = DEFAULT_CLOCK_SKEW,
    now: datetime | None = None,
) -> KnoxIdentity:
    if not public_key_pem or not public_key_pem.strip():
        raise KnoxJWTError("gateway_misconfigured", status=500)
    try:
        header = jwt.get_unverified_header(token)
    except jwt.exceptions.DecodeError as exc:
        raise KnoxJWTError("invalid_token") from exc
    alg = header.get("alg")
    if alg != expected_alg:
        raise KnoxJWTError("invalid_alg")

    leeway = max(int(clock_skew), 0)
    try:
        payload = jwt.decode(
            token,
            public_key_pem,
            algorithms=[expected_alg],
            leeway=leeway,
            options={
                "verify_signature": True,
                "verify_exp": True,
                "verify_nbf": True,
                "verify_iat": False,
                "verify_aud": False,
                "verify_iss": False,
                "require": [],
            },
        )
    except jwt.exceptions.ExpiredSignatureError as exc:
        raise KnoxJWTError("expired") from exc
    except jwt.exceptions.ImmatureSignatureError as extra:
        raise KnoxJWTError("not_yet_valid") from extra
    except jwt.exceptions.InvalidSignatureError as exc:
        raise KnoxJWTError("invalid_signature") from exc
    except jwt.exceptions.InvalidAlgorithmError as exc:
        raise KnoxJWTError("invalid_alg") from exc
    except jwt.exceptions.PyJWTError as exc:
        raise KnoxJWTError("invalid_token") from exc

    if issuer and payload.get("iss") != issuer:
        raise KnoxJWTError("invalid_issuer")
    sub = payload.get("sub")
    if not sub or not str(sub).strip():
        raise KnoxJWTError("invalid_subject")

    if now is None:
        now = datetime.now(timezone.utc)
    # Lua also applies clock_skew to exp/nbf; PyJWT leeway already did. Keep an explicit
    # exp check so missing-leeway libraries still fail closed.
    exp = payload.get("exp")
    if exp is not None:
        exp_ts = int(exp)
        if exp_ts + leeway < int(now.timestamp()):
            raise KnoxJWTError("expired")
    nbf = payload.get("nbf")
    if nbf is not None and int(nbf) > int(now.timestamp()) + leeway:
        raise KnoxJWTError("not_yet_valid")

    token_id = payload.get("knox.id")
    return KnoxIdentity(sub=str(sub), token_id=str(token_id) if token_id else None, payload=payload)


def www_authenticate(*, realm: str = DEFAULT_REALM, resource_metadata: str = "") -> str:
    value = f'Bearer realm="{realm}"'
    meta = (resource_metadata or "").strip()
    if meta:
        value = f'{value}, resource_metadata="{meta}"'
    return value


def unauthorized_headers(
    *,
    realm: str = DEFAULT_REALM,
    reason: str,
    resource_metadata: str = "",
) -> list[tuple[bytes, bytes]]:
    return [
        (b"www-authenticate", www_authenticate(realm=realm, resource_metadata=resource_metadata).encode("ascii")),
        (b"x-agent-gateway-reason", reason.encode("ascii")),
        (b"content-type", b"application/json"),
    ]


def assert_knox_token_enabled(
    identity: KnoxIdentity,
    *,
    token_state_url: str,
    token_state_host: str,
    tls_verify: bool = False,
    timeout: float = 2.0,
) -> None:
    """Fail closed when a managed Knox token is disabled. Empty URL skips the check."""
    base = (token_state_url or "").strip().rstrip("/")
    if not base:
        return
    managed = str(identity.payload.get("managed.token") or "").lower()
    if not identity.token_id:
        if managed in {"true", "1", "yes"}:
            raise KnoxJWTError("missing_token_id")
        return
    parsed = urlparse(base)
    host = (parsed.hostname or "").lower()
    expected = (token_state_host or "").split(":")[0].lower()
    if expected and host != expected:
        raise KnoxJWTError("token_state_unavailable")
    url = f"{base}/{identity.token_id}"
    try:
        response = httpx.get(
            url,
            headers={"Accept": "application/json"},
            timeout=timeout,
            verify=tls_verify,
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        raise KnoxJWTError("token_state_unavailable") from exc
    if response.status_code == 404:
        raise KnoxJWTError("revoked")
    if response.status_code != 200:
        raise KnoxJWTError("token_state_unavailable")
    try:
        body = response.json()
    except ValueError as exc:
        raise KnoxJWTError("token_state_unavailable") from exc
    if not isinstance(body, dict):
        raise KnoxJWTError("token_state_unavailable")
    enabled = body.get("enabled")
    if enabled is False or enabled == "false" or enabled == 0:
        raise KnoxJWTError("revoked")
