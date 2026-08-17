from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
_AGENT_PATH = ROOT / "examples" / "agent" / "mcp_agent.py"


def _load_mcp_agent():
    spec = importlib.util.spec_from_file_location("mcp_agent", _AGENT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["mcp_agent"] = module
    spec.loader.exec_module(module)
    return module


mcp_agent = _load_mcp_agent()


def test_profile_compose(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CDSW_DOMAIN", raising=False)
    monkeypatch.delenv("CDSW_PROJECT", raising=False)
    assert mcp_agent.profile() == "compose"


def test_profile_amp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CDSW_DOMAIN", "ml.example.com")
    assert mcp_agent.profile() == "amp"


def test_mcp_base_url_compose(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CDSW_DOMAIN", raising=False)
    monkeypatch.delenv("MCP_SPARK_URL", raising=False)
    assert mcp_agent.mcp_base_url("spark") == "http://127.0.0.1:9080/mcp/spark"


def test_mcp_base_url_amp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CDSW_DOMAIN", "ml.example.com")
    monkeypatch.delenv("MCP_HIVE_URL", raising=False)
    assert mcp_agent.mcp_base_url("hive") == "https://agent-gateway.ml.example.com/mcp/hive"


def test_agent_headers_compose_includes_caller_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CDSW_DOMAIN", raising=False)
    monkeypatch.setenv("KNOX_TOKEN", "test-token")
    monkeypatch.setenv("AGENT_CALLER_KEY", "lab-agent")
    headers = mcp_agent.agent_headers(adapter="spark")
    assert headers["Authorization"] == "Bearer test-token"
    assert headers["X-Agent-Key"] == "lab-agent"


def test_agent_headers_amp_includes_caller_key_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CDSW_DOMAIN", "ml.example.com")
    monkeypatch.setenv("KNOX_TOKEN", "test-token")
    monkeypatch.setenv("AGENT_CALLER_KEY", "lab-agent")
    headers = mcp_agent.agent_headers(adapter="spark")
    assert headers["X-Agent-Key"] == "lab-agent"


def test_agent_headers_amp_omits_caller_key_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CDSW_DOMAIN", "ml.example.com")
    monkeypatch.setenv("KNOX_TOKEN", "test-token")
    monkeypatch.delenv("AGENT_CALLER_KEY", raising=False)
    headers = mcp_agent.agent_headers(adapter="spark")
    assert "X-Agent-Key" not in headers


def test_tools_list_parses_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CDSW_DOMAIN", raising=False)
    monkeypatch.setenv("KNOX_TOKEN", "test-token")

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"tools": [{"name": "spark_list_batches"}]},
            }

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, url, json=None, headers=None):
            assert url == "http://127.0.0.1:9080/mcp/spark"
            assert json["method"] == "tools/list"
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    tools = mcp_agent.tools_list("spark")
    assert tools[0]["name"] == "spark_list_batches"


def test_tool_payload_decodes_content_text() -> None:
    payload = {"content": [{"text": json.dumps({"kind": "batches", "items": []})}]}
    assert mcp_agent.tool_payload(payload) == {"kind": "batches", "items": []}


def test_spark_job_uri_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPARK_FILE_URI", raising=False)
    assert mcp_agent.spark_job_uri("analyst") == "hdfs:///user/analyst/examples/count_to_10.py"


def test_spark_job_uri_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPARK_FILE_URI", "s3a://bucket/count_to_10.py")
    assert mcp_agent.spark_job_uri("analyst") == "s3a://bucket/count_to_10.py"


def test_poll_spark_batch_returns_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def fake_tools_call(name, arguments=None, **kwargs):
        calls["n"] += 1
        state = "starting" if calls["n"] == 1 else "success"
        return {
            "isError": False,
            "content": [{"text": json.dumps({"id": arguments["batch_id"], "state": state})}],
        }

    monkeypatch.setattr(mcp_agent, "tools_call", fake_tools_call)
    monkeypatch.setattr(mcp_agent.time, "sleep", lambda _s: None)
    result = mcp_agent.poll_spark_batch(3, timeout=30, interval=0)
    assert result["state"] == "success"
    assert calls["n"] == 2


def test_notebook_runs_spark_to_hive_example() -> None:
    nb = json.loads((ROOT / "examples/agent/third_party_agent.ipynb").read_text())
    source = "".join("".join(cell.get("source") or []) for cell in nb["cells"])
    assert "spark_submit_batch" in source
    assert "hive_select" in source
    assert "poll_spark_batch" in source
    assert "count_to_10" in source


def test_third_party_notebook_present() -> None:
    assert (ROOT / "examples/agent/third_party_agent.ipynb").is_file()
    assert _AGENT_PATH.is_file()
