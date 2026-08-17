from __future__ import annotations

import pytest

from sql import HiveError, describe_sql, ident, parse_limit, select_sql, show_tables_sql


def test_ident_rejects_injection() -> None:
    with pytest.raises(HiveError):
        ident("default; drop table x", field="database")
    with pytest.raises(HiveError):
        ident("a/b", field="table")
    with pytest.raises(HiveError):
        ident("*", field="column")


def test_show_tables_and_describe_are_quoted() -> None:
    assert show_tables_sql("analytics") == "SHOW TABLES IN `analytics`"
    assert describe_sql("analytics", "events") == "DESCRIBE `analytics`.`events`"


def test_select_requires_columns_and_caps_limit() -> None:
    sql, lim = select_sql("default", "dual", ["dummy_col"], 10)
    assert sql == "SELECT `dummy_col` FROM `default`.`dual` LIMIT 10"
    assert lim == 10
    with pytest.raises(HiveError, match="SELECT \\*"):
        select_sql("default", "dual", [], 10)
    with pytest.raises(HiveError, match="between 1 and"):
        parse_limit(500)
