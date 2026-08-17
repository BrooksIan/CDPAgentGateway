from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_sql():
    path = ROOT / "mcp-impala" / "sql.py"
    spec = importlib.util.spec_from_file_location("mcp_impala_sql", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sql = _load_sql()


def test_ident_rejects_injection() -> None:
    with pytest.raises(sql.ImpalaError):
        sql.ident("default; drop table x", field="database")
    with pytest.raises(sql.ImpalaError):
        sql.ident("a/b", field="table")
    with pytest.raises(sql.ImpalaError):
        sql.ident("*", field="column")


def test_show_tables_and_describe_are_quoted() -> None:
    assert sql.show_tables_sql("analytics") == "SHOW TABLES IN `analytics`"
    assert sql.describe_sql("analytics", "events") == "DESCRIBE `analytics`.`events`"


def test_select_requires_columns_and_caps_limit() -> None:
    statement, lim = sql.select_sql("default", "dual", ["dummy_col"], 10)
    assert statement == "SELECT `dummy_col` FROM `default`.`dual` LIMIT 10"
    assert lim == 10
    with pytest.raises(sql.ImpalaError, match="SELECT \\*"):
        sql.select_sql("default", "dual", [], 10)
    with pytest.raises(sql.ImpalaError, match="between 1 and"):
        sql.parse_limit(500)
