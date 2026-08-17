from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_hs2():
    extra = str(ROOT / "mcp-impala")
    saved = {name: sys.modules.get(name) for name in ("sql", "hs2")}
    sys.path.insert(0, extra)
    for name in ("sql", "hs2"):
        sys.modules.pop(name, None)
    try:
        spec = importlib.util.spec_from_file_location("mcp_impala_hs2", ROOT / "mcp-impala" / "hs2.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if sys.path and sys.path[0] == extra:
            sys.path.pop(0)
        for name in ("sql", "hs2"):
            sys.modules.pop(name, None)
            previous = saved[name]
            if previous is not None:
                sys.modules[name] = previous


hs2 = _load_hs2()


def test_impala_error_from_hs2_maps_missing_table() -> None:
    exc = hs2.impala_error_from_hs2(
        RuntimeError("AnalysisException: Could not resolve table reference: 'ibrooks.count_to_10'")
    )
    assert exc.status == 404
    assert "count_to_10" in str(exc)
    assert "Bearer" not in str(exc)


def test_impala_error_from_hs2_maps_privileges() -> None:
    exc = hs2.impala_error_from_hs2(RuntimeError("AuthorizationException: User does not have privileges"))
    assert exc.status == 403


def test_impala_error_from_hs2_maps_http_401() -> None:
    exc = hs2.impala_error_from_hs2(RuntimeError("HTTP code 401: Unauthorized"))
    assert exc.status == 401
    assert "401" in str(exc)
    assert "Bearer" not in str(exc)


def test_impala_error_from_hs2_hides_impyla_close_bug() -> None:
    exc = hs2.impala_error_from_hs2(AttributeError("'NoneType' object has no attribute 'close'"))
    assert exc.status == 502
    assert "NoneType" not in str(exc)
    assert "HS2" in str(exc)


def test_impala_error_from_hs2_strips_bearer() -> None:
    exc = hs2.impala_error_from_hs2(RuntimeError("Authorization: Bearer abc.def"))
    assert str(exc) == "RuntimeError"
    assert exc.status == 502


def test_describe_skips_partition_footer_rows() -> None:
    columns = hs2._describe_columns(
        [
            ("n", "bigint", ""),
            ("# Partition Information", None, None),
            ("# col_name", "data_type", "comment"),
        ]
    )
    assert columns == [{"name": "n", "type": "bigint", "comment": ""}]


def test_is_mock_false_when_cdw_host_set(monkeypatch) -> None:
    monkeypatch.setenv("UPSTREAM_HOST", "mock-cdp")
    monkeypatch.setenv(
        "IMPALA_HOST",
        "coordinator-default-impala-aws.dw-go01-demo-aws.ylcu-atmi.cloudera.site",
    )
    monkeypatch.setenv("IMPALA_HTTP_PATH", "cliservice")
    monkeypatch.setenv("IMPALA_SCHEME", "https")
    monkeypatch.setenv("IMPALA_PORT", "443")
    assert hs2.is_mock() is False
    kwargs = hs2._connect_kwargs("dummy-token")
    assert kwargs["host"].startswith("coordinator-default-impala-aws")
    assert kwargs["http_path"] == "cliservice"
    assert kwargs["auth_mechanism"] == "JWT"


def test_is_mock_true_without_cdw_or_knox(monkeypatch) -> None:
    monkeypatch.setenv("UPSTREAM_HOST", "mock-cdp")
    monkeypatch.delenv("IMPALA_HOST", raising=False)
    assert hs2.is_mock() is True
