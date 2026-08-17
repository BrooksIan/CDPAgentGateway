from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from store import (
    admit,
    check_quota,
    connect,
    get_audit,
    list_events,
    overview,
    parse_day,
    record_event,
    set_quota,
    usage_today,
)


def test_default_quota_is_unlimited(tmp_path: Path) -> None:
    db = connect(tmp_path / "gw.sqlite")
    decision = check_quota(db, "analyst", "spark_submit_batch")
    assert decision["allowed"] is True
    assert decision["quota"]["daily_submits"] is None


def test_submit_quota_denies_and_records(tmp_path: Path) -> None:
    db = connect(tmp_path / "gw.sqlite")
    set_quota(db, "analyst", daily_submits=1, daily_calls=None)
    record_event(db, sub="analyst", tool="spark_submit_batch", kind="call", ok=True)
    decision = admit(db, sub="analyst", tool="spark_submit_batch", request_id="r1")
    assert decision["allowed"] is False
    assert decision["reason"] == "submit_quota"
    assert usage_today(db, "analyst")["denied"] == 1


def test_call_quota_applies_to_reads(tmp_path: Path) -> None:
    db = connect(tmp_path / "gw.sqlite")
    set_quota(db, "*", daily_calls=1, daily_submits=None)
    record_event(db, sub="analyst", tool="spark_list_batches", kind="call", ok=True)
    decision = check_quota(db, "analyst", "spark_list_sessions")
    assert decision["allowed"] is False
    assert decision["reason"] == "call_quota"


def test_per_user_override_beats_default(tmp_path: Path) -> None:
    db = connect(tmp_path / "gw.sqlite")
    set_quota(db, "*", daily_submits=0)
    set_quota(db, "analyst", daily_submits=5)
    decision = check_quota(db, "analyst", "spark_submit_batch")
    assert decision["allowed"] is True
    assert decision["quota"]["daily_submits"] == 5


def test_audit_joins_tool_sub_knox_id_and_request_id(tmp_path: Path) -> None:
    db = connect(tmp_path / "gw.sqlite")
    record_event(
        db,
        sub="analyst",
        tool="spark_list_batches",
        kind="call",
        ok=True,
        request_id="p2-04-join",
        token_id="knox-token-uuid",
    )
    audit = get_audit(db, "p2-04-join")
    assert audit is not None
    assert audit["tool"] == "spark_list_batches"
    assert audit["sub"] == "analyst"
    assert audit["knox.id"] == "knox-token-uuid"
    assert audit["request_id"] == "p2-04-join"
    dumped = json.dumps(audit)
    assert "Bearer" not in dumped
    assert "eyJ" not in dumped
    assert get_audit(db, "") is None
    assert get_audit(db, "missing") is None


def test_audit_does_not_store_jwt_shaped_ids(tmp_path: Path) -> None:
    db = connect(tmp_path / "gw.sqlite")
    jwt = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.aaa.bbb"
    record_event(
        db,
        sub="analyst",
        tool="spark_list_sessions",
        kind="call",
        ok=True,
        request_id=jwt,
        token_id="Bearer secret",
    )
    assert get_audit(db, jwt) is None


def test_parse_day_rejects_junk() -> None:
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        parse_day("17-08-2026")
    assert parse_day("") is None
    assert parse_day("2024-01-02") == "2024-01-02"


def test_events_and_overview_honor_utc_day_and_result(tmp_path: Path) -> None:
    db = connect(tmp_path / "gw.sqlite")
    past = datetime(2024, 1, 2, 15, 30, tzinfo=timezone.utc)
    record_event(
        db,
        sub="analyst",
        tool="spark_list_batches",
        kind="call",
        ok=True,
        request_id="old-day",
        at=past,
    )
    record_event(
        db,
        sub="analyst",
        tool="spark_submit_batch",
        kind="denied",
        ok=False,
        request_id="quota-now",
        status=429,
    )
    assert overview(db, day="2024-01-02")["calls"] == 1
    assert overview(db, day="2024-01-02")["denied"] == 0
    assert overview(db)["denied"] == 1
    old = list_events(db, day="2024-01-02", tool="spark_list_batches")
    assert [row["request_id"] for row in old] == ["old-day"]
    assert list_events(db, day="2024-01-02", result="quota") == []
    quota = list_events(db, result="quota")
    assert quota[0]["request_id"] == "quota-now"
    assert list_events(db, result="ok", day="2024-01-02")[0]["tool"] == "spark_list_batches"
    with pytest.raises(ValueError, match="result"):
        list_events(db, result="burst")
