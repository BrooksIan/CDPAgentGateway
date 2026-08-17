from __future__ import annotations

from pathlib import Path

import jwt
import pytest

from unittest.mock import Mock, patch

from agentgateway.keys import fetch_pinned_knox_pubkey, generate_test_keys
from agentgateway.knox_jwt import (
    KnoxJWTError,
    assert_knox_token_enabled,
    extract_bearer,
    unauthorized_headers,
    verify_knox_jwt,
    www_authenticate,
)
from agentgateway.token import knox_claims, sign_rs256, unsigned_token

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def public_pem() -> str:
    generate_test_keys()
    return (ROOT / "conf" / "keys" / "public.pem").read_text()


def test_extract_bearer_rejects_missing_and_basic() -> None:
    with pytest.raises(KnoxJWTError) as missing:
        extract_bearer(None)
    assert missing.value.reason == "missing_token"
    with pytest.raises(KnoxJWTError) as basic:
        extract_bearer("Basic Zm9vOmJhcg==")
    assert basic.value.reason == "missing_token"
    with pytest.raises(KnoxJWTError) as empty:
        extract_bearer("Bearer")
    assert empty.value.reason == "missing_token"


def test_alg_none_is_rejected(public_pem: str) -> None:
    with pytest.raises(KnoxJWTError) as exc:
        verify_knox_jwt(unsigned_token(), public_key_pem=public_pem)
    assert exc.value.reason == "invalid_alg"


def test_hs256_algorithm_confusion_is_rejected(public_pem: str) -> None:
    token = jwt.encode(knox_claims(), "algorithm-confusion-test-secret-32b", algorithm="HS256")
    with pytest.raises(KnoxJWTError) as exc:
        verify_knox_jwt(token, public_key_pem=public_pem)
    assert exc.value.reason == "invalid_alg"


def test_expired_token_is_rejected(public_pem: str) -> None:
    with pytest.raises(KnoxJWTError) as exc:
        verify_knox_jwt(sign_rs256(knox_claims(expires_in=-120)), public_key_pem=public_pem)
    assert exc.value.reason == "expired"


def test_wrong_issuer_is_rejected(public_pem: str) -> None:
    with pytest.raises(KnoxJWTError) as exc:
        verify_knox_jwt(sign_rs256(knox_claims(issuer="someone-else")), public_key_pem=public_pem)
    assert exc.value.reason == "invalid_issuer"


def test_malformed_token_is_rejected(public_pem: str) -> None:
    with pytest.raises(KnoxJWTError) as exc:
        verify_knox_jwt("not-a-jwt", public_key_pem=public_pem)
    assert exc.value.reason == "invalid_token"


def test_valid_knox_shaped_token(public_pem: str) -> None:
    identity = verify_knox_jwt(sign_rs256(knox_claims(sub="analyst")), public_key_pem=public_pem)
    assert identity.sub == "analyst"
    assert identity.token_id == "test-token-id"


def test_fetch_pinned_jwks_refuses_foreign_host(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Refusing token jku host"):
        fetch_pinned_knox_pubkey(
            knox_proxy_url="https://knox.example.com/gateway/cdp-proxy-token/livy_for_spark3/",
            jwks_url="https://evil.example/jwks.json",
            out=tmp_path / "knox-public.pem",
        )


def test_www_authenticate_includes_resource_metadata() -> None:
    value = www_authenticate(
        realm="knox",
        resource_metadata="http://127.0.0.1:9080/.well-known/oauth-protected-resource",
    )
    assert value.startswith("Bearer realm=\"knox\"")
    assert "resource_metadata=" in value
    headers = unauthorized_headers(
        reason="missing_token",
        resource_metadata="http://127.0.0.1:9080/.well-known/oauth-protected-resource",
    )
    www = dict(headers)[b"www-authenticate"].decode("ascii")
    assert "resource_metadata=" in www


def test_assert_knox_token_enabled_rejects_revoked(public_pem: str) -> None:
    identity = verify_knox_jwt(sign_rs256(knox_claims(sub="analyst")), public_key_pem=public_pem)
    response = Mock()
    response.status_code = 200
    response.json.return_value = {"enabled": False}
    with patch("agentgateway.knox_jwt.httpx.get", return_value=response) as get:
        with pytest.raises(KnoxJWTError) as exc:
            assert_knox_token_enabled(
                identity,
                token_state_url="http://mock-cdp:8080/gateway/homepage/knoxtoken/api/v2/token/state",
                token_state_host="mock-cdp",
            )
    assert exc.value.reason == "revoked"
    assert "Authorization" not in get.call_args.kwargs["headers"]


def test_assert_knox_token_enabled_pins_host(public_pem: str) -> None:
    identity = verify_knox_jwt(sign_rs256(knox_claims(sub="analyst")), public_key_pem=public_pem)
    with pytest.raises(KnoxJWTError) as exc:
        assert_knox_token_enabled(
            identity,
            token_state_url="http://evil.example/token/state",
            token_state_host="mock-cdp",
        )
    assert exc.value.reason == "token_state_unavailable"


def test_assert_knox_token_enabled_skips_when_unconfigured(public_pem: str) -> None:
    identity = verify_knox_jwt(sign_rs256(knox_claims(sub="analyst")), public_key_pem=public_pem)
    assert_knox_token_enabled(identity, token_state_url="", token_state_host="mock-cdp")
