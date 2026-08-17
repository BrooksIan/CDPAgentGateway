from __future__ import annotations

from unittest.mock import Mock, patch

import httpx
import pytest

from quota import QuotaDenied, admit, record


def test_admit_raises_on_429() -> None:
    response = Mock()
    response.status_code = 429
    response.headers = {"content-type": "application/json"}
    response.json.return_value = {"allowed": False, "reason": "submit_quota"}
    with patch("quota.httpx.post", return_value=response):
        with pytest.raises(QuotaDenied):
            admit(sub="analyst", tool="spark_submit_batch", request_id="r", token_id=None)


def test_admit_fails_open_when_admin_down() -> None:
    with patch("quota.httpx.post", side_effect=httpx.ConnectError("down")):
        assert admit(sub="analyst", tool="spark_list_batches", request_id=None, token_id=None) is None


def test_record_swallows_transport_errors() -> None:
    with patch("quota.httpx.post", side_effect=httpx.ConnectError("down")):
        record(sub="analyst", tool="spark_list_batches", ok=True, request_id=None, token_id=None)
