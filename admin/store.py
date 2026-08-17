"""SQLite usage and quota store keyed by Knox sub. Never stores bearers."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_SUB = "*"
WRITE_TOOLS = frozenset({"spark_submit_batch"})
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
EVENT_RESULTS = frozenset({"ok", "quota", "error"})


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_day(now: datetime | None = None) -> str:
    return (now or utc_now()).strftime("%Y-%m-%d")


def parse_day(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if not _DAY.match(text):
        raise ValueError("day must be YYYY-MM-DD")
    return text


def connect(path: str | Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(path), check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    init_schema(db)
    return db


def init_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            day TEXT NOT NULL,
            sub TEXT NOT NULL,
            token_id TEXT,
            tool TEXT NOT NULL,
            request_id TEXT,
            ok INTEGER,
            kind TEXT NOT NULL,
            status INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_events_day_sub ON events(day, sub);
        CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
        CREATE INDEX IF NOT EXISTS idx_events_request_id ON events(request_id);
        CREATE TABLE IF NOT EXISTS quotas (
            sub TEXT PRIMARY KEY,
            daily_calls INTEGER,
            daily_submits INTEGER,
            updated_at TEXT NOT NULL
        );
        """
    )
    db.commit()
    row = db.execute("SELECT 1 FROM quotas WHERE sub = ?", (DEFAULT_SUB,)).fetchone()
    if row is None:
        db.execute(
            "INSERT INTO quotas (sub, daily_calls, daily_submits, updated_at) VALUES (?, NULL, NULL, ?)",
            (DEFAULT_SUB, utc_now().isoformat(timespec="seconds")),
        )
        db.commit()


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    number = int(value)
    if number < 0:
        raise ValueError("quota must be >= 0")
    return number


def set_quota(
    db: sqlite3.Connection,
    sub: str,
    *,
    daily_calls: Any = None,
    daily_submits: Any = None,
) -> dict[str, Any]:
    name = (sub or "").strip() or DEFAULT_SUB
    if len(name) > 256:
        raise ValueError("sub is too long")
    payload = {
        "sub": name,
        "daily_calls": _int_or_none(daily_calls),
        "daily_submits": _int_or_none(daily_submits),
        "updated_at": utc_now().isoformat(timespec="seconds"),
    }
    db.execute(
        """
        INSERT INTO quotas (sub, daily_calls, daily_submits, updated_at)
        VALUES (:sub, :daily_calls, :daily_submits, :updated_at)
        ON CONFLICT(sub) DO UPDATE SET
            daily_calls = excluded.daily_calls,
            daily_submits = excluded.daily_submits,
            updated_at = excluded.updated_at
        """,
        payload,
    )
    db.commit()
    return payload


def delete_quota(db: sqlite3.Connection, sub: str) -> bool:
    name = (sub or "").strip()
    if not name or name == DEFAULT_SUB:
        raise ValueError("cannot delete the default quota")
    cur = db.execute("DELETE FROM quotas WHERE sub = ?", (name,))
    db.commit()
    return cur.rowcount > 0


def get_quota(db: sqlite3.Connection, sub: str) -> dict[str, Any]:
    name = (sub or "").strip() or DEFAULT_SUB
    row = db.execute("SELECT * FROM quotas WHERE sub = ?", (name,)).fetchone()
    default = db.execute("SELECT * FROM quotas WHERE sub = ?", (DEFAULT_SUB,)).fetchone()
    inherited = False
    source = row
    if source is None:
        source = default
        inherited = True
    return {
        "sub": name,
        "daily_calls": source["daily_calls"] if source else None,
        "daily_submits": source["daily_submits"] if source else None,
        "inherited": inherited,
        "updated_at": source["updated_at"] if source else None,
    }


def usage_today(db: sqlite3.Connection, sub: str, *, day: str | None = None) -> dict[str, int]:
    day = day or utc_day()
    name = (sub or "").strip()
    calls = db.execute(
        "SELECT COUNT(*) AS n FROM events WHERE day = ? AND sub = ? AND kind = 'call'",
        (day, name),
    ).fetchone()["n"]
    submits = db.execute(
        """
        SELECT COUNT(*) AS n FROM events
        WHERE day = ? AND sub = ? AND kind = 'call' AND tool = 'spark_submit_batch'
        """,
        (day, name),
    ).fetchone()["n"]
    denied = db.execute(
        "SELECT COUNT(*) AS n FROM events WHERE day = ? AND sub = ? AND kind = 'denied'",
        (day, name),
    ).fetchone()["n"]
    errors = db.execute(
        "SELECT COUNT(*) AS n FROM events WHERE day = ? AND sub = ? AND kind = 'call' AND ok = 0",
        (day, name),
    ).fetchone()["n"]
    return {"calls": int(calls), "submits": int(submits), "denied": int(denied), "errors": int(errors)}


def check_quota(db: sqlite3.Connection, sub: str, tool: str) -> dict[str, Any]:
    name = (sub or "").strip() or "unknown"
    quota = get_quota(db, name)
    usage = usage_today(db, name)
    reason = None
    if tool in WRITE_TOOLS and quota["daily_submits"] is not None and usage["submits"] >= quota["daily_submits"]:
        reason = "submit_quota"
    elif quota["daily_calls"] is not None and usage["calls"] >= quota["daily_calls"]:
        reason = "call_quota"
    return {
        "allowed": reason is None,
        "reason": reason,
        "sub": name,
        "tool": tool,
        "usage": usage,
        "quota": {"daily_calls": quota["daily_calls"], "daily_submits": quota["daily_submits"]},
    }


def _safe_id(value: str | None) -> str | None:
    text = (value or "").strip()[:128]
    if not text:
        return None
    lowered = text.lower()
    if lowered.startswith("bearer ") or (text.count(".") == 2 and text.startswith("eyJ")):
        return None
    return text


def record_event(
    db: sqlite3.Connection,
    *,
    sub: str,
    tool: str,
    kind: str,
    ok: bool | None = None,
    request_id: str | None = None,
    token_id: str | None = None,
    status: int | None = None,
    at: datetime | None = None,
) -> dict[str, Any]:
    now = at or utc_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    name = (sub or "").strip() or "unknown"
    tool_name = (tool or "").strip() or "unknown"
    if len(name) > 256 or len(tool_name) > 128:
        raise ValueError("event fields too long")
    payload = {
        "ts": now.isoformat(timespec="seconds"),
        "day": utc_day(now),
        "sub": name,
        "token_id": _safe_id(token_id),
        "tool": tool_name[:128],
        "request_id": _safe_id(request_id),
        "ok": None if ok is None else (1 if ok else 0),
        "kind": kind,
        "status": status,
    }
    db.execute(
        """
        INSERT INTO events (ts, day, sub, token_id, tool, request_id, ok, kind, status)
        VALUES (:ts, :day, :sub, :token_id, :tool, :request_id, :ok, :kind, :status)
        """,
        payload,
    )
    db.commit()
    return payload


def admit(db: sqlite3.Connection, *, sub: str, tool: str, request_id: str | None = None, token_id: str | None = None) -> dict[str, Any]:
    decision = check_quota(db, sub, tool)
    if not decision["allowed"]:
        record_event(
            db,
            sub=sub,
            tool=tool,
            kind="denied",
            ok=False,
            request_id=request_id,
            token_id=token_id,
            status=429,
        )
    return decision


def overview(db: sqlite3.Connection, *, day: str | None = None) -> dict[str, Any]:
    day = day or utc_day()
    totals = db.execute(
        """
        SELECT
            SUM(CASE WHEN kind = 'call' THEN 1 ELSE 0 END) AS calls,
            SUM(CASE WHEN tool = 'spark_submit_batch' AND kind = 'call' THEN 1 ELSE 0 END) AS submits,
            SUM(CASE WHEN kind = 'denied' THEN 1 ELSE 0 END) AS denied,
            SUM(CASE WHEN kind = 'call' AND ok = 0 THEN 1 ELSE 0 END) AS errors,
            COUNT(DISTINCT sub) AS users
        FROM events
        WHERE day = ?
        """,
        (day,),
    ).fetchone()
    default = get_quota(db, DEFAULT_SUB)
    return {
        "day": day,
        "users": int(totals["users"] or 0),
        "calls": int(totals["calls"] or 0),
        "submits": int(totals["submits"] or 0),
        "denied": int(totals["denied"] or 0),
        "errors": int(totals["errors"] or 0),
        "default_quota": default,
    }


def list_users(db: sqlite3.Connection, *, day: str | None = None) -> list[dict[str, Any]]:
    day = day or utc_day()
    rows = db.execute(
        """
        SELECT sub,
               COUNT(*) AS events,
               SUM(CASE WHEN kind = 'call' THEN 1 ELSE 0 END) AS calls,
               SUM(CASE WHEN kind = 'call' AND tool = 'spark_submit_batch' THEN 1 ELSE 0 END) AS submits,
               SUM(CASE WHEN kind = 'denied' THEN 1 ELSE 0 END) AS denied,
               SUM(CASE WHEN kind = 'call' AND ok = 0 THEN 1 ELSE 0 END) AS errors,
               MAX(ts) AS last_seen,
               MAX(tool) AS last_tool
        FROM events
        WHERE day = ?
        GROUP BY sub
        ORDER BY last_seen DESC
        """,
        (day,),
    ).fetchall()
    last_tools = {
        row["sub"]: db.execute(
            "SELECT tool FROM events WHERE day = ? AND sub = ? ORDER BY id DESC LIMIT 1",
            (day, row["sub"]),
        ).fetchone()["tool"]
        for row in rows
    }
    users: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        quota = get_quota(db, row["sub"])
        usage = {
            "calls": int(row["calls"] or 0),
            "submits": int(row["submits"] or 0),
            "denied": int(row["denied"] or 0),
            "errors": int(row["errors"] or 0),
        }
        users.append(
            {
                "sub": row["sub"],
                "usage": usage,
                "quota": quota,
                "last_seen": row["last_seen"],
                "last_tool": last_tools.get(row["sub"]),
            }
        )
        seen.add(row["sub"])
    extra = db.execute(
        "SELECT sub, daily_calls, daily_submits, updated_at FROM quotas WHERE sub != ? ORDER BY sub",
        (DEFAULT_SUB,),
    ).fetchall()
    for row in extra:
        if row["sub"] in seen:
            continue
        users.append(
            {
                "sub": row["sub"],
                "usage": {"calls": 0, "submits": 0, "denied": 0, "errors": 0},
                "quota": get_quota(db, row["sub"]),
                "last_seen": None,
                "last_tool": None,
            }
        )
    return users


def list_events(
    db: sqlite3.Connection,
    *,
    limit: int = 50,
    sub: str | None = None,
    tool: str | None = None,
    result: str | None = None,
    day: str | None = None,
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 200))
    clauses = ["day = ?"]
    params: list[Any] = [parse_day(day) or utc_day()]
    name = (sub or "").strip()
    if name:
        clauses.append("sub = ?")
        params.append(name)
    tool_name = (tool or "").strip()
    if tool_name:
        clauses.append("tool = ?")
        params.append(tool_name)
    kind = (result or "").strip()
    if kind:
        if kind not in EVENT_RESULTS:
            raise ValueError("result must be ok, quota, or error")
        if kind == "quota":
            clauses.append("kind = 'denied'")
        elif kind == "error":
            clauses.append("kind = 'call' AND ok = 0")
        else:
            clauses.append("kind = 'call' AND ok = 1")
    params.append(limit)
    rows = db.execute(
        f"SELECT * FROM events WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT ?",
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def audit_record(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    ok = data.get("ok")
    return {
        "request_id": data.get("request_id"),
        "sub": data.get("sub"),
        "knox.id": data.get("token_id"),
        "tool": data.get("tool"),
        "kind": data.get("kind"),
        "ok": None if ok is None else bool(ok),
        "ts": data.get("ts"),
        "status": data.get("status"),
    }


def get_audit(db: sqlite3.Connection, request_id: str) -> dict[str, Any] | None:
    rid = (request_id or "").strip()
    if not rid or len(rid) > 128:
        return None
    row = db.execute(
        "SELECT * FROM events WHERE request_id = ? ORDER BY id DESC LIMIT 1",
        (rid,),
    ).fetchone()
    if row is None:
        return None
    return audit_record(row)


def list_quotas(db: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = db.execute("SELECT * FROM quotas ORDER BY CASE WHEN sub = '*' THEN 0 ELSE 1 END, sub").fetchall()
    return [dict(row) for row in rows]
