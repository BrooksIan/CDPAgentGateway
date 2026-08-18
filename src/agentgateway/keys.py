from __future__ import annotations

import json
import ssl
import urllib.error
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


def jwks_url_candidates(primary: str) -> list[str]:
    """Public Cloud Knox often serves JWKS at api/v2; parse_knox_proxy_url still emits v1."""
    ordered: list[str] = []
    for url in (primary,):
        text = (url or "").strip()
        if text and text not in ordered:
            ordered.append(text)
        if "/api/v1/jwks.json" in text:
            alt = text.replace("/api/v1/jwks.json", "/api/v2/jwks.json")
            if alt not in ordered:
                ordered.append(alt)
        elif "/api/v2/jwks.json" in text:
            alt = text.replace("/api/v2/jwks.json", "/api/v1/jwks.json")
            if alt not in ordered:
                ordered.append(alt)
    return ordered


def fetch_knox_pubkey(jwks_url: str, out: Path, *, insecure: bool = False) -> Path:
    context = ssl._create_unverified_context() if insecure else ssl.create_default_context()
    try:
        with urllib.request.urlopen(jwks_url, context=context, timeout=15) as response:
            raw = response.read()
            status = getattr(response, "status", 200)
    except urllib.error.HTTPError as extra:
        snippet = extra.read()[:80].decode("utf-8", "replace").replace("\n", " ")
        raise ValueError(f"JWKS HTTP {extra.code} from {jwks_url}: {snippet!r}") from extra
    except urllib.error.URLError as extra:
        raise ValueError(f"JWKS unreachable {jwks_url}: {extra.reason}") from extra
    text = raw.decode("utf-8", "replace").lstrip("\ufeff").strip()
    if status >= 400 or not text or text[0] not in "{[":
        raise ValueError(f"JWKS URL did not return JSON (HTTP {status}): {jwks_url}")
    try:
        body = json.loads(text)
    except json.JSONDecodeError as extra:
        raise ValueError(f"JWKS URL did not return JSON: {jwks_url}") from extra
    keys = body.get("keys") or []
    if not keys:
        raise ValueError("JWKS document contained no keys")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(jwk_to_pem(keys[0]))
    return out


def fetch_pinned_knox_pubkey(
    *,
    knox_proxy_url: str,
    jwks_url: str | None,
    out: Path,
    insecure: bool = False,
) -> Path:
    """Download JWKS only when the URL host matches the inventory Knox host."""
    from agentgateway.knox import parse_knox_proxy_url, trusted_jku

    parsed = parse_knox_proxy_url(knox_proxy_url)
    explicit = (jwks_url or "").strip()
    candidates: list[str] = []
    for url in (*jwks_url_candidates(explicit), *jwks_url_candidates(parsed.get("KNOX_JWKS_URL") or "")):
        if url not in candidates:
            candidates.append(url)
    if not candidates:
        raise ValueError("Need KNOX_JWKS_URL or a Knox proxy URL that implies one")
    errors: list[str] = []
    for url in candidates:
        trusted_jku(url, parsed["UPSTREAM_HOST"])
        try:
            return fetch_knox_pubkey(url, out, insecure=insecure)
        except ValueError as extra:
            errors.append(str(extra))
    raise ValueError("Knox JWKS was not JSON. Tried: " + " | ".join(errors[:4]))
