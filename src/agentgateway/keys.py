from __future__ import annotations

import json
import ssl
import urllib.request
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
from jwt.utils import from_base64url_uint, to_base64url_uint

from agentgateway.paths import repo_root


def key_dir() -> Path:
    path = repo_root() / "conf" / "keys"
    path.mkdir(parents=True, exist_ok=True)
    return path


def generate_test_keys(*, force: bool = False) -> Path:
    directory = key_dir()
    private_path = directory / "private.pem"
    public_path = directory / "public.pem"
    jwks_path = directory / "jwks.json"
    if private_path.exists() and public_path.exists() and jwks_path.exists() and not force:
        return directory

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    public = key.public_key()
    public_path.write_bytes(
        public.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    numbers = public.public_numbers()
    jwks = {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": "local-test",
                "n": to_base64url_uint(numbers.n).decode("ascii"),
                "e": to_base64url_uint(numbers.e).decode("ascii"),
            }
        ]
    }
    jwks_path.write_text(json.dumps(jwks, indent=2) + "\n")
    return directory


def jwk_to_pem(jwk: dict) -> bytes:
    if jwk.get("kty") != "RSA":
        raise ValueError(f"Unsupported kty: {jwk.get('kty')}")
    numbers = RSAPublicNumbers(from_base64url_uint(jwk["e"]), from_base64url_uint(jwk["n"]))
    key = numbers.public_key()
    return key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def fetch_knox_pubkey(jwks_url: str, out: Path, *, insecure: bool = False) -> Path:
    context = ssl._create_unverified_context() if insecure else ssl.create_default_context()
    with urllib.request.urlopen(jwks_url, context=context, timeout=15) as response:
        body = json.loads(response.read().decode("utf-8"))
    keys = body.get("keys") or []
    if not keys:
        raise ValueError("JWKS document contained no keys")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(jwk_to_pem(keys[0]))
    return out
