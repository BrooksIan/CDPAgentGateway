from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "examples" / "impala" / "README.md"


def test_impala_example_shows_named_select() -> None:
    text = README.read_text()
    assert "impala_select" in text
    assert "impala_list_tables" in text
    assert "impala_describe_table" in text
    assert "count_to_10" in text
    assert "--arg columns=n" in text
    assert "--arg limit=10" in text
    assert "No `SELECT *`" in text or "named columns" in text.lower()
