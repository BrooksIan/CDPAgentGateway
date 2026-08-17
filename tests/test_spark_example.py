from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "spark" / "count_to_10.py"


def test_count_to_10_example_is_valid_python() -> None:
    source = EXAMPLE.read_text()
    ast.parse(source)
    assert "range(1, 11)" in source
    assert "count=" in source
    assert "SparkSession" in source
    assert "iceberg" in source
    assert "hive_select" in source
    assert "enableHiveSupport" in source
