from __future__ import annotations

from hs2 import hive_error_from_hs2


def test_hive_error_from_hs2_maps_missing_table() -> None:
    exc = hive_error_from_hs2(
        RuntimeError(
            "Error while compiling statement: FAILED: SemanticException "
            "[Error 10001]: Line 1:16 Table not found 'count_to_10'"
        )
    )
    assert exc.status == 404
    assert "count_to_10" in str(exc)
    assert "Bearer" not in str(exc)


def test_hive_error_from_hs2_strips_bearer() -> None:
    exc = hive_error_from_hs2(RuntimeError("Authorization: Bearer abc.def"))
    assert str(exc) == "RuntimeError"
    assert exc.status == 502
