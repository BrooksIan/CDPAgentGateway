from __future__ import annotations

import jwt
import pytest

from jwt_util import knox_claims, sign_rs256, unsigned_token

pytestmark = pytest.mark.gateway


def test_health_is_public(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers.get("X-Request-Id")


@pytest.mark.parametrize(
    ("headers", "reason"),
    [
        ({}, "missing_token"),
        ({"Authorization": "Basic Zm9vOmJhcg=="}, "missing_token"),
        ({"Authorization": "Bearer"}, "missing_token"),
        ({"Authorization": "Bearer not-a-jwt"}, "invalid_token"),
    ],
)
def test_missing_or_malformed_token_is_rejected(client, headers, reason) -> None:
    response = client.get("/cdp/livy_for_spark3/sessions", headers=headers)
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate", "").startswith("Bearer")
    assert response.json()["reason"] == reason


def test_alg_none_is_rejected(client) -> None:
    token = unsigned_token()
    response = client.get("/cdp/livy_for_spark3/sessions", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["reason"] in {"invalid_alg", "invalid_token", "invalid_signature"}


def test_hs256_algorithm_confusion_is_rejected(client) -> None:
    token = jwt.encode(knox_claims(), "algorithm-confusion-test-secret-32b", algorithm="HS256")
    response = client.get("/cdp/livy_for_spark3/sessions", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["reason"] in {"invalid_alg", "invalid_signature"}


def test_expired_token_is_rejected(client) -> None:
    token = sign_rs256(knox_claims(expires_in=-120))
    response = client.get("/cdp/livy_for_spark3/sessions", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["reason"] in {"expired", "invalid_signature"}


def test_wrong_issuer_is_rejected(client) -> None:
    token = sign_rs256(knox_claims(issuer="someone-else"))
    response = client.get("/cdp/livy_for_spark3/sessions", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["reason"] == "invalid_issuer"


def test_valid_knox_shaped_token_is_accepted(client) -> None:
    token = sign_rs256(knox_claims(sub="analyst"))
    response = client.get("/cdp/livy_for_spark3/sessions", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["knox_user"] == "analyst"
    assert body["x_knox_user"] == "analyst"
    assert body["authorization_present"] is True
    assert body["token_id"] == "test-token-id"
    assert body["sessions"] == []
