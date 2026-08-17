from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from livy import (
    LivyError,
    build_submit_body,
    livy_relpath,
    normalize_list,
    parse_batch_id,
    public_batch,
    public_livy_message,
    redact_secrets,
    truncate_log,
)


def test_livy_paths_are_allowlisted() -> None:
    assert livy_relpath("sessions") == "/sessions"
    assert livy_relpath("batches") == "/batches"
    assert livy_relpath("batch", 12) == "/batches/12"
    assert livy_relpath("log", 12) == "/batches/12/log"


def test_livy_rejects_bad_batch_id() -> None:
    with pytest.raises(LivyError):
        livy_relpath("batch", -1)
    with pytest.raises(LivyError):
        parse_batch_id("1e2")
    with pytest.raises(LivyError):
        parse_batch_id(True)


def test_normalize_list_caps_items() -> None:
    items = [{"id": i, "state": "success", "secret": "nope"} for i in range(40)]
    shaped = normalize_list({"from": 0, "total": 40, "sessions": items}, kind="batches")
    assert shaped["returned"] == 25
    assert shaped["truncated"] is True
    assert "secret" not in shaped["items"][0]
    assert shaped["items"][0]["id"] == 0


def test_truncate_log_caps_lines_and_chars() -> None:
    payload = {"id": 0, "from": 0, "log": ["x" * 200] * 200}
    shaped = truncate_log(payload)
    assert shaped["truncated"] is True
    assert len(shaped["log"]) <= 80
    assert sum(len(line) for line in shaped["log"]) <= 8000 + 80


def test_truncate_log_redacts_jwt_shaped_strings() -> None:
    jwt = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJhIn0.signaturepart"
    shaped = truncate_log({"id": 0, "log": [f"Bonded to Knox token {jwt}"]})
    joined = "\n".join(shaped["log"])
    assert jwt not in joined
    assert "[redacted]" in joined
    assert redact_secrets(f"Authorization: Bearer {jwt}").endswith("[redacted]")


def test_public_batch_drops_unknown_fields() -> None:
    assert public_batch({"id": 1, "state": "dead", "conf": {"password": "x"}}) == {
        "id": 1,
        "state": "dead",
    }


def test_get_json_forwards_authorization_and_not_html() -> None:
    from livy import get_json

    response = Mock()
    response.status_code = 200
    response.headers = {"content-type": "application/json"}
    response.json.return_value = {"from": 0, "total": 0, "sessions": []}
    with patch("livy.httpx.get", return_value=response) as get:
        shaped = get_json("sessions", authorization="Bearer test-token", knox_user="analyst")
    headers = get.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer test-token"
    assert shaped["kind"] == "sessions"
    assert shaped["knox_user"] == "analyst"
    assert get.call_args.kwargs["follow_redirects"] is False


def test_submit_body_requires_cluster_file_uri() -> None:
    """P2-07/P2-08/P2-09/P2-10 guards, without APISIX."""
    body = build_submit_body({"file": "hdfs:///user/analyst/job.py", "args": "10"})
    assert body["file"].startswith("hdfs://")
    assert body["args"] == ["10"]
    comma = build_submit_body({"file": "hdfs:///user/analyst/job.py", "args": "analyst,count_to_10"})
    assert comma["args"] == ["analyst", "count_to_10"]
    with pytest.raises(LivyError):
        build_submit_body({"file": "https://evil.example/job.py"})
    with pytest.raises(LivyError):
        build_submit_body({"file": "hdfs:///job.py", "proxyUser": "hive"})
    with pytest.raises(LivyError):
        build_submit_body({"file": "hdfs:///job.py", "code": "print(1)"})
    with pytest.raises(LivyError):
        build_submit_body({"file": "hdfs:///job.py", "conf": {"spark.yarn.keytab": "x"}})


def test_public_livy_message_skips_secrets() -> None:
    assert public_livy_message({"msg": "requirement failed: bad args"}) == "requirement failed: bad args"
    assert public_livy_message({"message": "Authorization: Bearer abc"}) is None


def test_post_json_includes_livy_message_on_400() -> None:
    from livy import post_json

    response = Mock()
    response.status_code = 400
    response.headers = {"content-type": "application/json"}
    response.json.return_value = {"msg": "Cannot parse args"}
    with patch("livy.httpx.post", return_value=response):
        with pytest.raises(LivyError) as exc:
            post_json(
                "batches",
                authorization="Bearer test-token",
                payload={"file": "hdfs:///user/analyst/job.py"},
            )
    assert exc.value.status == 400
    assert exc.value.details["livy_message"] == "Cannot parse args"
    assert "Bearer" not in str(exc.value.details)
