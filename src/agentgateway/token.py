from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt

from agentgateway.paths import repo_root


def _read(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Missing {path}; run: gateway init")
    return path.read_text()


def private_key() -> str:
    return _read(repo_root() / "conf" / "keys" / "private.pem")


def public_key() -> str:
    return _read(repo_root() / "conf" / "keys" / "public.pem")


def knox_claims(
    sub: str = "analyst",
    issuer: str = "KNOXSSO",
    expires_in: int = 3600,
    extra: dict | None = None,
) -> dict:
    now = datetime.now(timezone.utc)
    claims = {
        "sub": sub,
        "iss": issuer,
        "exp": now + timedelta(seconds=expires_in),
        "iat": now,
        "knox.id": "test-token-id",
        "managed.token": "true",
    }
    if extra:
        claims.update(extra)
    return claims


def sign_rs256(claims: dict | None = None, headers: dict | None = None) -> str:
    return jwt.encode(
        claims or knox_claims(),
        private_key(),
        algorithm="RS256",
        headers=headers or {"kid": "local-test", "typ": "JWT"},
    )


def inspect_bearer(token: str) -> dict:
    raw = token.strip()
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    if raw.count(".") != 2:
        raise ValueError("Not a JWT (expected three dot-separated parts)")
    header = jwt.get_unverified_header(raw)
    payload = jwt.decode(raw, options={"verify_signature": False})
    alg = header.get("alg")
    if alg != "RS256":
        raise ValueError(f"Expected alg=RS256, got {alg}")
    iss = payload.get("iss")
    if iss != "KNOXSSO":
        raise ValueError(f"Expected iss=KNOXSSO, got {iss}")
    if not payload.get("sub"):
        raise ValueError("Token is missing sub")
    return {"token": raw, "header": header, "payload": payload}


def public_claims(payload: dict, header: dict) -> dict[str, str]:
    exp = payload.get("exp")
    return {
        "sub": str(payload.get("sub", "")),
        "iss": str(payload.get("iss", "")),
        "aud": str(payload.get("aud", "")),
        "alg": str(header.get("alg", "")),
        "exp": str(exp or ""),
        "knox.id": str(payload.get("knox.id", "")),
        "managed.token": str(payload.get("managed.token", "")),
        "kid": str(header.get("kid", "")),
        "jku": str(header.get("jku") or payload.get("jku") or ""),
    }


def unsigned_token(claims: dict | None = None) -> str:
    payload = claims or knox_claims()
    header = {"alg": "none", "typ": "JWT"}

    def b64(data: dict) -> str:
        raw = json.dumps(
            data,
            separators=(",", ":"),
            default=lambda value: int(value.timestamp()) if isinstance(value, datetime) else value,
        ).encode("utf-8")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    return f"{b64(header)}.{b64(payload)}."
