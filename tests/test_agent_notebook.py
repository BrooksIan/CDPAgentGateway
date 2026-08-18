from __future__ import annotations

import importlib.util
import json
import os
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
        status_code = 200
        text = ""

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


def test_mcp_request_includes_http_error_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CDSW_DOMAIN", raising=False)
    monkeypatch.setenv("KNOX_TOKEN", "test-token")

    class FakeResponse:
        status_code = 500
        text = '{"error": "gateway_misconfigured", "reason": "gateway_misconfigured"}'

        def json(self) -> dict:
            raise AssertionError("json() should not run on HTTP 500")

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, url, json=None, headers=None):
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    with pytest.raises(RuntimeError, match="gateway_misconfigured"):
        mcp_agent.tools_list("spark")


def test_mcp_request_explains_invalid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CDSW_DOMAIN", raising=False)
    monkeypatch.setenv("KNOX_TOKEN", "test-token")

    class FakeResponse:
        status_code = 401
        text = '{"error": "unauthorized", "reason": "invalid_token"}'

        def json(self) -> dict:
            raise AssertionError("json() should not run")

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, url, json=None, headers=None):
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    with pytest.raises(RuntimeError, match="not parse as a JWT"):
        mcp_agent.tools_list("spark")


def test_load_knox_token_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KNOX_TOKEN", "session-jwt")
    assert mcp_agent.load_knox_token(prompt=False) == "session-jwt"


def test_normalize_knox_token_strips_bearer_quotes_and_wrap() -> None:
    raw = '  "Bearer eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJhIn0.sig"  \n'
    assert mcp_agent.normalize_knox_token(raw) == "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJhIn0.sig"


def test_knox_token_status_rejects_passcode() -> None:
    status = mcp_agent.knox_token_status("knox-passcode")
    assert status["set"] is True
    assert status["jwt_shaped"] is False
    assert "invalid_token" in status["hint"]
    assert "knox-passcode" not in json.dumps(status)


def test_knox_token_status_reads_unverified_claims() -> None:
    token = (
        "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiJhbmFseXN0IiwiaXNzIjoiS05PWFNTT19URVNUIiwiZXhwIjoyMTQ3NDgzNjQ3fQ."
        "c2ln"
    )
    status = mcp_agent.knox_token_status(token)
    assert status["jwt_shaped"] is True
    assert status["alg"] == "RS256"
    assert status["sub"] == "analyst"
    assert "token" not in status
    dumped = json.dumps(status)
    assert token not in dumped


def test_load_knox_token_reprompts_when_env_is_not_jwt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KNOX_TOKEN", "passcode")
    monkeypatch.delenv("KNOX_TOKEN_FILE", raising=False)
    monkeypatch.setattr(mcp_agent, "getpass", lambda _prompt: "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJhIn0.sig")
    token = mcp_agent.load_knox_token(prompt=True, ignore_non_jwt_env=True)
    assert mcp_agent.jwt_shaped(token)
    assert os.environ["KNOX_TOKEN"] == token


def test_load_knox_token_prompts_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KNOX_TOKEN", raising=False)
    monkeypatch.delenv("KNOX_TOKEN_FILE", raising=False)
    monkeypatch.setattr(mcp_agent, "getpass", lambda _prompt: "pasted-jwt")
    assert mcp_agent.load_knox_token(prompt=True) == "pasted-jwt"
    assert os.environ["KNOX_TOKEN"] == "pasted-jwt"
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
    assert "load_knox_token" in source
    assert "knox_token_status" in source
    assert "getpass" in source or "Knox JWT" in source
    assert "langgraph_agent.ipynb" in source
    assert "print(token)" not in source


def test_notebooks_have_cell_ids() -> None:
    for rel in (
        "examples/agent/third_party_agent.ipynb",
        "examples/agent/langgraph_agent.ipynb",
    ):
        nb = json.loads((ROOT / rel).read_text())
        missing = [i for i, cell in enumerate(nb["cells"]) if not cell.get("id")]
        assert missing == [], f"{rel} cells missing id: {missing}"


def test_third_party_notebook_present() -> None:
    assert (ROOT / "examples/agent/third_party_agent.ipynb").is_file()
    assert _AGENT_PATH.is_file()


def test_adapter_for_tool_routes_prefixes() -> None:
    assert mcp_agent.adapter_for_tool("spark_submit_batch") == "spark"
    assert mcp_agent.adapter_for_tool("hive_select") == "hive"
    assert mcp_agent.adapter_for_tool("impala_list_databases") == "impala"
    with pytest.raises(ValueError, match="unknown tool"):
        mcp_agent.adapter_for_tool("not_a_tool")


def test_call_gateway_tool_routes_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_tools_call(name, arguments=None, **kwargs):
        seen["name"] = name
        seen["arguments"] = arguments
        seen["adapter"] = kwargs.get("adapter")
        return {
            "isError": False,
            "content": [{"text": json.dumps({"ok": True, "tool": name})}],
        }

    monkeypatch.setattr(mcp_agent, "tools_call", fake_tools_call)
    payload = mcp_agent.call_gateway_tool("hive_list_tables", {"database": "analyst"})
    assert payload["ok"] is True
    assert seen["name"] == "hive_list_tables"
    assert seen["adapter"] == "hive"
    assert seen["arguments"] == {"database": "analyst"}


def test_list_gateway_tools_tags_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_tools_list(adapter: str):
        return [{"name": f"{adapter}_list_databases", "description": adapter}]

    monkeypatch.setattr(mcp_agent, "tools_list", fake_tools_list)
    catalog = mcp_agent.list_gateway_tools(("hive", "impala"))
    assert [item["adapter"] for item in catalog] == ["hive", "impala"]
    assert catalog[0]["name"] == "hive_list_databases"


def _load_langgraph_mcp():
    path = ROOT / "examples" / "agent" / "langgraph_mcp.py"
    spec = importlib.util.spec_from_file_location("langgraph_mcp", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["langgraph_mcp"] = module
    spec.loader.exec_module(module)
    return module


def test_langgraph_notebook_present() -> None:
    nb_path = ROOT / "examples/agent/langgraph_agent.ipynb"
    assert nb_path.is_file()
    assert (ROOT / "examples/agent/langgraph_mcp.py").is_file()
    nb = json.loads(nb_path.read_text())
    source = "".join("".join(cell.get("source") or []) for cell in nb["cells"])
    assert "langchain_tools" in source
    assert "make_agent" in source
    assert "invoke_agent" in source
    assert "load_knox_token" in source
    assert "knox_token_status" in source
    assert "getpass" in source or "Knox JWT" in source
    assert "install_langgraph_deps" in source
    assert "show_model_form" in source
    assert "apply_model_form" in source
    assert "Model URL" in source
    assert "Model ID" in source
    assert "Model token" in source
    assert "LANGGRAPH_RUN_SUBMIT" in source
    assert "Streamable HTTP" in source
    assert "print(token)" not in source
    assert "[langgraph]" not in source
    assert "langchain-core 1.x" in source or "langchain-core>=0.3.85,<0.4" in source


def test_langgraph_coerce_and_last_message() -> None:
    lg = _load_langgraph_mcp()
    schema = {
        "type": "object",
        "properties": {"batch_id": {"type": "integer"}},
        "required": ["batch_id"],
    }
    assert lg._coerce_arguments({"batch_id": "7", "unused": None}, schema) == {"batch_id": 7}

    class Msg:
        def __init__(self, type: str, content: object, **extra: object) -> None:
            self.type = type
            self.content = content
            for key, value in extra.items():
                setattr(self, key, value)

    result = {
        "messages": [
            Msg("human", "list databases"),
            Msg("ai", "", tool_calls=[{"name": "hive_list_databases"}]),
            Msg("tool", "{}", name="hive_list_databases"),
            Msg("ai", "Hive databases: default"),
        ]
    }
    assert lg.last_ai_text(result) == "Hive databases: default"
    assert lg.tool_names_used(result) == ["hive_list_databases", "hive_list_databases"]
    assert "Knox" in lg.SYSTEM_PROMPT
    assert "bearer" in lg.SYSTEM_PROMPT.lower()


def test_langchain_tools_call_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("langchain_core")
    pytest.importorskip("pydantic")
    lg = _load_langgraph_mcp()
    monkeypatch.setenv("KNOX_TOKEN", "test-token")
    catalog = [
        {
            "name": "hive_list_databases",
            "description": "List Hive databases",
            "adapter": "hive",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        }
    ]
    seen: dict[str, object] = {}

    def fake_call(name, arguments=None, **kwargs):
        seen["name"] = name
        seen["adapter"] = kwargs.get("adapter")
        seen["arguments"] = arguments
        return {"items": ["default"]}

    monkeypatch.setattr(lg, "call_gateway_tool", fake_call)
    tools = lg.langchain_tools(catalog=catalog)
    assert len(tools) == 1
    assert tools[0].name == "hive_list_databases"
    payload = json.loads(tools[0].invoke({}))
    assert payload == {"items": ["default"]}
    assert seen["name"] == "hive_list_databases"
    assert seen["adapter"] == "hive"


def test_schema_model_required_integer() -> None:
    pytest.importorskip("pydantic")
    lg = _load_langgraph_mcp()
    model = lg.schema_model(
        "spark_get_batch",
        {
            "type": "object",
            "properties": {"batch_id": {"type": "integer"}},
            "required": ["batch_id"],
        },
    )
    assert model(batch_id=3).batch_id == 3


def test_langgraph_extra_pins_core_03() -> None:
    text = (ROOT / "pyproject.toml").read_text()
    assert "langchain-core>=0.3.85,<0.4" in text
    assert "langgraph>=0.3.5,<0.4" in text
    constraints = (ROOT / "examples/agent/langgraph-constraints.txt").read_text()
    assert "langchain-core>=0.3.85,<0.4" in constraints
    assert not any(line.startswith("protobuf") for line in constraints.splitlines())


def test_langgraph_pip_packages_amp_keeps_core_03(monkeypatch: pytest.MonkeyPatch) -> None:
    lg = _load_langgraph_mcp()
    monkeypatch.setattr(lg, "_dist_version", lambda _name: "0.3.85")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LANGGRAPH_INSTALL_ANTHROPIC", raising=False)
    packages = lg.langgraph_pip_packages(amp=True)
    assert all(not item.startswith("langchain-core") for item in packages)
    assert any(item.startswith("langgraph") and "<0.4" in item for item in packages)
    assert all(not item.startswith("langchain-anthropic") for item in packages)
    assert all("protobuf" not in item for item in packages)


def test_langgraph_pip_packages_amp_downgrades_core_1(monkeypatch: pytest.MonkeyPatch) -> None:
    lg = _load_langgraph_mcp()
    monkeypatch.setattr(lg, "_dist_version", lambda _name: "1.5.6")
    packages = lg.langgraph_pip_packages(amp=True)
    assert any(item.startswith("langchain-core") and "<0.4" in item for item in packages)


def test_langgraph_pip_packages_amp_skips_protobuf(monkeypatch: pytest.MonkeyPatch) -> None:
    lg = _load_langgraph_mcp()

    def fake_version(name: str) -> str | None:
        if name == "langchain-core":
            return "0.3.85"
        if name == "protobuf":
            return "7.34.0"
        return None

    monkeypatch.setattr(lg, "_dist_version", fake_version)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LANGGRAPH_INSTALL_ANTHROPIC", raising=False)
    packages = lg.langgraph_pip_packages(amp=True)
    assert all("protobuf" not in item for item in packages)


def test_require_langchain_core_03_rejects_1x(monkeypatch: pytest.MonkeyPatch) -> None:
    lg = _load_langgraph_mcp()
    monkeypatch.setattr(lg, "_dist_version", lambda _name: "1.5.6")
    with pytest.raises(RuntimeError, match="1.x"):
        lg.require_langchain_core_03()


def test_apply_model_settings_omits_token(monkeypatch: pytest.MonkeyPatch) -> None:
    lg = _load_langgraph_mcp()
    monkeypatch.delenv("MODEL_URL", raising=False)
    monkeypatch.delenv("MODEL_ID", raising=False)
    monkeypatch.delenv("MODEL_TOKEN", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    status = lg.apply_model_settings(
        url="https://llm.example/v1/",
        model_id="llama-8b",
        token="secret-model-token",
    )
    assert status["url"] == "https://llm.example/v1"
    assert status["model_id"] == "llama-8b"
    assert status["token_set"] is True
    assert "secret-model-token" not in json.dumps(status)
    assert os.environ["MODEL_TOKEN"] == "secret-model-token"


def test_chat_model_uses_form_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    lg = _load_langgraph_mcp()
    monkeypatch.setenv("MODEL_URL", "https://llm.example/v1")
    monkeypatch.setenv("MODEL_ID", "llama-8b")
    monkeypatch.setenv("MODEL_TOKEN", "secret-model-token")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    seen: dict[str, object] = {}

    def fake_chat(**kwargs):
        seen.update(kwargs)
        return "llm"

    monkeypatch.setattr(lg, "_make_chat_openai", fake_chat)
    assert lg.chat_model() == "llm"
    assert seen["model"] == "llama-8b"
    assert seen["base_url"] == "https://llm.example/v1"
    assert seen["api_key"] == "secret-model-token"


def test_should_omit_auto_tool_choice_on_custom_url(monkeypatch: pytest.MonkeyPatch) -> None:
    lg = _load_langgraph_mcp()
    monkeypatch.setenv("MODEL_URL", "https://vllm.example/v1")
    monkeypatch.delenv("MODEL_TOOL_CHOICE", raising=False)
    assert lg._should_omit_auto_tool_choice("https://vllm.example/v1") is True
    monkeypatch.setenv("MODEL_TOOL_CHOICE", "auto")
    assert lg._should_omit_auto_tool_choice("https://vllm.example/v1") is False
    monkeypatch.setenv("MODEL_TOOL_CHOICE", "omit")
    monkeypatch.delenv("MODEL_URL", raising=False)
    assert lg._should_omit_auto_tool_choice(None) is True


def test_set_llm_method_bypasses_pydantic() -> None:
    lg = _load_langgraph_mcp()

    class FakeModel:
        model_config = {"extra": "forbid"}

        def __setattr__(self, name: str, value: object) -> None:
            raise ValueError(f'"{type(self).__name__}" object has no field "{name}"')

        def bind_tools(self, tools, **kwargs):
            return tools

    llm = FakeModel()
    lg._set_llm_method(llm, "bind_tools", lambda self, tools, **kwargs: ("patched", tools))
    assert llm.bind_tools(["x"])[0] == "patched"


def test_omit_vllm_auto_tool_choice_strips_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    lg = _load_langgraph_mcp()
    monkeypatch.setenv("MODEL_URL", "https://vllm.example/v1")
    monkeypatch.delenv("MODEL_TOOL_CHOICE", raising=False)

    class Dummy:
        def bind_tools(self, tools, *, tool_choice=None, **kwargs):
            return {"tools": tools, "tool_choice": tool_choice, **kwargs}

        def _get_request_payload(self, *_args, **_kwargs):
            return {"model": "x", "tools": [], "tool_choice": "auto"}

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            return kwargs

    dummy = Dummy()
    patched = lg._omit_vllm_auto_tool_choice(dummy)
    payload = patched._get_request_payload([])
    assert payload.get("tool_choice") != "auto"
    assert "tool_choice" not in payload
    bound = patched.bind_tools(["hive_list_databases"], tool_choice="auto")
    assert bound.get("tool_choice") is None


def test_invoke_agent_explains_vllm_auto_tool_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    lg = _load_langgraph_mcp()
    monkeypatch.setenv("KNOX_TOKEN", "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJhIn0.sig")

    class Boom:
        def invoke(self, *_args, **_kwargs):
            raise RuntimeError(
                '"auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set'
            )

    monkeypatch.setattr(lg, "make_agent", lambda: Boom())
    with pytest.raises(RuntimeError, match="omits tool_choice=auto"):
        lg.invoke_agent("list databases", agent=Boom())


def test_apply_model_form_reads_widget_values(monkeypatch: pytest.MonkeyPatch) -> None:
    lg = _load_langgraph_mcp()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    class Box:
        def __init__(self, value: str) -> None:
            self.value = value

    status = lg.apply_model_form(
        {
            "url": Box("https://models.example/v1"),
            "model_id": Box("qwen"),
            "token": Box("secret-model-token-xyz"),
        }
    )
    assert status["hint"] == "ok"
    assert status["url"] == "https://models.example/v1"
    assert status["model_id"] == "qwen"
    assert "secret-model-token-xyz" not in json.dumps(status)

