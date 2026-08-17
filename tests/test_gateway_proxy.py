from __future__ import annotations

import pytest

from jwt_util import knox_claims, sign_rs256

pytestmark = pytest.mark.gateway


def test_livy_sessions_are_proxied_for_authenticated_user(client) -> None:
    token = sign_rs256(knox_claims(sub="analyst"))
    response = client.get(
        "/cdp/livy_for_spark3/sessions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["knox_user"] == "analyst"
    assert body["sessions"] == []


def test_non_spark_cdp_path_is_not_proxied(client) -> None:
    token = sign_rs256(knox_claims(sub="analyst"))
    response = client.get("/cdp/hive", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 404


def test_unknown_spark_path_does_not_leak_through_unauthenticated(client) -> None:
    response = client.get("/cdp/livy_for_spark3/sessions")
    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"


def test_cdp_routes_forward_request_id(client) -> None:
    token = sign_rs256()
    response = client.get(
        "/cdp/livy_for_spark3/sessions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.headers.get("X-Request-Id")


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/cdp/livy_for_spark3/sessions/0/statements"),
        ("POST", "/cdp/livy_for_spark3/batches"),
        ("PUT", "/cdp/livy_for_spark3/sessions/0"),
        ("DELETE", "/cdp/livy_for_spark3/batches/0"),
    ],
)
def test_livy_http_writes_are_not_proxied(client, method: str, path: str) -> None:
    token = sign_rs256(knox_claims(sub="analyst"))
    kwargs: dict = {"headers": {"Authorization": f"Bearer {token}"}}
    if method == "POST":
        kwargs["headers"]["Content-Type"] = "application/json"
        kwargs["json"] = {"code": "print(1)"}
    response = client.request(method, path, **kwargs)
    assert response.status_code in {404, 405}, response.text


def test_webhdfs_list_is_proxied_for_authenticated_user(client) -> None:
    token = sign_rs256(knox_claims(sub="analyst"))
    response = client.get(
        "/cdp/webhdfs/v1/",
        params={"op": "LISTSTATUS"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["knox_user"] == "analyst"
    assert "FileStatuses" in body


def test_webhdfs_mkdir_put_is_proxied(client) -> None:
    token = sign_rs256(knox_claims(sub="analyst"))
    headers = {"Authorization": f"Bearer {token}"}
    mkdir = client.put(
        "/cdp/webhdfs/v1/user/analyst/examples",
        params={"op": "MKDIRS"},
        headers=headers,
    )
    assert mkdir.status_code == 200, mkdir.text
    assert mkdir.json().get("boolean") is True


def test_webhdfs_delete_is_not_proxied(client) -> None:
    token = sign_rs256(knox_claims(sub="analyst"))
    response = client.delete(
        "/cdp/webhdfs/v1/user/analyst/examples/job.py",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code in {404, 405}


def test_webhdfs_requires_bearer(client) -> None:
    response = client.get("/cdp/webhdfs/v1/", params={"op": "LISTSTATUS"})
    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"
