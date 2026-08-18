from __future__ import annotations

from unittest.mock import Mock, patch

import httpx
import pytest

from quota import QuotaDenied, admit, record
import quota


def test_admit_raises_on_429(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_BACKEND", "http")
    response = Mock()
    response.status_code = 429
    response.headers = {"content-type": "application/json"}
    response.json.return_value = {"allowed": False, "reason": "submit_quota"}
    with patch.object(quota.httpx, "post", return_value=response):
        with pytest.raises(QuotaDenied):
            admit(sub="analyst", tool="spark_submit_batch", request_id="r", token_id=None)


def test_admit_fails_open_when_admin_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_BACKEND", "http")
    with patch.object(quota.httpx, "post", side_effect=httpx.ConnectError("down")):
        assert admit(sub="analyst", tool="spark_list_batches", request_id=None, token_id=None) is None


def test_admit_sqlite_backend_enforces_quota(tmp_path, monkeypatch) -> None:
    from store import connect, set_quota

    db_path = tmp_path / "gateway.sqlite"
    monkeypatch.setenv("ADMIN_BACKEND", "sqlite")
    monkeypatch.setenv("ADMIN_DB", str(db_path))
    quota._sqlite_cache.clear()
    db = connect(db_path)
    set_quota(db, "analyst", daily_calls=0, daily_submits=0)
    with pytest.raises(QuotaDenied):
        admit(sub="analyst", tool="spark_list_batches", request_id="r1", token_id=None)


def test_record_swallows_transport_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_BACKEND", "http")
    with patch.object(quota.httpx, "post", side_effect=httpx.ConnectError("down")):
        record(sub="analyst", tool="spark_list_batches", ok=True, request_id=None, token_id=None)
