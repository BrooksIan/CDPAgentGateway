"""LangGraph ReAct bindings for CDP Agent Gateway MCP (POST JSON-RPC only).

This is a third-party agent sample. It wraps `tools/list` / `tools/call` as LangChain
tools and runs `langgraph.prebuilt.create_react_agent`. It does not use Streamable HTTP,
stdio MCP, or `langchain-mcp-adapters` transports the gateway does not implement.

Never log or print the Knox bearer. The JWT stays in `mcp_agent` headers.
"""

from __future__ import annotations

import inspect
import json
import os
import re
import subprocess
import sys
import time
import uuid
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from mcp_agent import (
    adapter_for_tool,
    call_gateway_tool,
    list_gateway_tools,
    load_knox_token,
)

# Local / CML-0.3 workbenches: LangGraph 0.3 + langchain-core 0.3.x. Never install 1.x.
# AMP workbenches that still ship langchain 0.2.x stay on LangGraph 0.2 + core 0.2.x.
LANGGRAPH_PIP_PACKAGES = (
    "langgraph>=0.3.5,<0.4",
    "langchain-core>=0.3.85,<0.4",
    "langchain-openai>=0.3,<0.4",
)
_LANGGRAPH_PIP_02 = (
    "langgraph>=0.2.27,<0.3",
    "langchain-core>=0.2.43,<0.3",
    "langchain-openai>=0.1.22,<0.3",
    "langsmith>=0.1.112,<0.2",
)
_ANTHROPIC_EXTRA = "langchain-anthropic>=0.3,<0.4"
_ANTHROPIC_02 = "langchain-anthropic>=0.1.23,<0.3"
_AMP_OPENAI = "openai>=1.104.2,<2"
_CONSTRAINTS = Path(__file__).resolve().parent / "langgraph-constraints.txt"
_CONSTRAINTS_02 = Path(__file__).resolve().parent / "langgraph-constraints-02.txt"

_TERMINAL_BATCH = {"success", "dead", "killed", "error"}

SYSTEM_PROMPT = (
    "You are a third-party agent calling Cloudera Data Platform through CDP Agent Gateway. "
    "Use only the bound MCP tools. Do not call Knox, Livy, HiveServer2, Impala, Ozone, or NiFi "
    "hostnames. Do not request or print the bearer token. "
    "spark_submit_batch is a write as the Knox JWT subject; file must be hdfs://, s3a://, "
    "abfs://, or o3fs://. After submit, poll spark_get_batch until success or dead. "
    "Hive and Impala tools are read-only: named columns only, limit at most 50, no SELECT *, "
    "no WHERE, no DDL/DML. /cdp/hive and /cdp/impala are unpublished."
)


def _dist_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def _amp_runtime() -> bool:
    return bool(os.environ.get("CDSW_PROJECT") or os.environ.get("CDSW_DOMAIN"))


def langchain_core_line(*, amp: bool | None = None) -> str:
    """AMP follows CML's langchain metapackage (0.2 vs 0.3). Local Compose stays on 0.3."""
    forced = (os.environ.get("LANGGRAPH_CORE_LINE") or "").strip()
    if forced in {"0.2", "0.3"}:
        return forced
    if amp is None:
        amp = _amp_runtime()
    if not amp:
        return "0.3"
    langchain = _dist_version("langchain") or ""
    if langchain.startswith("0.2."):
        return "0.2"
    core = _dist_version("langchain-core") or ""
    if core.startswith("0.2."):
        return "0.2"
    return "0.3"


def langgraph_pip_packages(*, amp: bool | None = None) -> list[str]:
    """Package specs that match the workbench LangChain line (0.2 or 0.3).

    AMP does not install or pin protobuf (CML plugins disagree: mlflow 4.25.3 vs raz-client 7.34.0).
    AMP skips Anthropic unless ANTHROPIC_API_KEY or LANGGRAPH_INSTALL_ANTHROPIC is set.
    """
    if amp is None:
        amp = _amp_runtime()
    line = langchain_core_line(amp=amp)
    if line == "0.2":
        packages = list(_LANGGRAPH_PIP_02)
        anthropic = _ANTHROPIC_02
    else:
        packages = list(LANGGRAPH_PIP_PACKAGES)
        anthropic = _ANTHROPIC_EXTRA
        if amp:
            core = _dist_version("langchain-core")
            if core and core.startswith("0.3."):
                packages = [item for item in packages if not item.startswith("langchain-core")]
            packages.append(_AMP_OPENAI)
    if amp:
        want_anthropic = bool(
            (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("LANGGRAPH_INSTALL_ANTHROPIC") or "").strip()
        )
        if want_anthropic:
            packages.append(anthropic)
    else:
        packages.append(anthropic)
    return packages


def require_langchain_core() -> str:
    """Fail closed on langchain-core 1.x. AMP 0.2 runtimes must stay on core 0.2.x."""
    line = langchain_core_line()
    core = _dist_version("langchain-core")
    if not core:
        raise RuntimeError("langchain-core is not installed")
    parts = core.split(".")
    major = int(parts[0])
    minor = int(parts[1]) if len(parts) > 1 else 0
    if major >= 1:
        raise RuntimeError(
            f"langchain-core {core} is 1.x; this notebook needs 0.2.x or 0.3.x. "
            "Restart this session, then re-run the install cell. Do not pip install langchain-core 1.x on AMP."
        )
    want_minor = 2 if line == "0.2" else 3
    if major != 0 or minor != want_minor:
        raise RuntimeError(
            f"langchain-core {core} is not 0.{want_minor}.x. Re-run the install cell."
        )
    loaded = sys.modules.get("langchain_core")
    loaded_ver = getattr(loaded, "__version__", "") if loaded is not None else ""
    if loaded_ver.startswith("1."):
        raise RuntimeError(
            "This kernel still has langchain-core 1.x imported. Restart the kernel and re-run."
        )
    if loaded_ver and not loaded_ver.startswith(f"0.{want_minor}."):
        raise RuntimeError(
            f"This kernel still has langchain-core {loaded_ver} imported; disk is {core}. "
            "Restart this Workbench session, then re-run from the first cell."
        )
    return core


def require_langchain_core_03() -> str:
    return require_langchain_core()


def install_langgraph_deps(*, root: Path | None = None) -> list[str]:
    """Install LangGraph to match CML langchain (0.2 or 0.3). Never install core 1.x."""
    amp = _amp_runtime()
    line = langchain_core_line(amp=amp)
    packages = langgraph_pip_packages(amp=amp)
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade-strategy",
        "only-if-needed",
    ]
    if amp:
        cmd.append("--user")
    constraints = _CONSTRAINTS_02 if line == "0.2" else _CONSTRAINTS
    if constraints.is_file():
        cmd.extend(["-c", str(constraints)])
    cmd.extend(packages)
    core_before = _dist_version("langchain-core") or ""
    if amp and line == "0.2" and core_before.startswith("0.3."):
        print(
            "CML langchain is 0.2.x; pinning langchain-core back to 0.2.x "
            "(undoes a previous 0.3 install that conflicted with langchain-aws)."
        )
    print("langgraph pip:", " ".join(packages))
    subprocess.check_call(cmd, cwd=str(root or Path.cwd()))
    print("langchain-core:", require_langchain_core())
    print("langgraph line:", line)
    if amp:
        print("note: AMP does not change protobuf. Ignore remaining CML typing-extensions pins.")
    return packages


def _python_type(spec: dict[str, Any]) -> Any:
    raw = spec.get("type")
    if isinstance(raw, list):
        non_null = [item for item in raw if item != "null"]
        inner = _python_type({**spec, "type": non_null[0] if non_null else "string"})
        return inner | None if "null" in raw else inner
    if raw == "array":
        items = spec.get("items") if isinstance(spec.get("items"), dict) else {"type": "string"}
        return list[_python_type(items)]
    mapping = {"string": str, "integer": int, "number": float, "boolean": bool}
    return mapping.get(raw or "string", str)


def schema_model(tool_name: str, schema: dict[str, Any] | None) -> type:
    """Build a Pydantic args model from an MCP JSON Schema (used by LangChain tools)."""
    from pydantic import Field, create_model

    schema = schema or {"type": "object"}
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = set(schema.get("required") or [])
    fields: dict[str, Any] = {}
    for key, spec in properties.items():
        spec = spec if isinstance(spec, dict) else {}
        py_type = _python_type(spec)
        description = spec.get("description")
        if key in required:
            fields[key] = (py_type, Field(..., description=description))
        else:
            fields[key] = (py_type | None, Field(default=None, description=description))
    model_name = "".join(part.title() for part in tool_name.split("_") if part) + "Input"
    return create_model(model_name, **fields)


def _coerce_arguments(arguments: dict[str, Any], schema: dict[str, Any] | None) -> dict[str, Any]:
    properties = (schema or {}).get("properties") if isinstance((schema or {}).get("properties"), dict) else {}
    coerced: dict[str, Any] = {}
    for key, value in (arguments or {}).items():
        if value is None:
            continue
        spec = properties.get(key) if isinstance(properties.get(key), dict) else {}
        if spec.get("type") == "integer" and isinstance(value, str) and value.isdigit():
            coerced[key] = int(value)
            continue
        coerced[key] = value
    return coerced


def _invoke_mcp(name: str, adapter: str, schema: dict[str, Any] | None, timeout: float, **kwargs: Any) -> str:
    payload = call_gateway_tool(
        name,
        _coerce_arguments(kwargs, schema),
        adapter=adapter,
        timeout=timeout,
    )
    if name == "spark_get_batch":
        state = str(payload.get("state") or "").lower()
        if state and state not in _TERMINAL_BATCH:
            time.sleep(max(float(os.environ.get("SPARK_POLL_INTERVAL", "10")), 1.0))
    return json.dumps(payload)


def langchain_tools(
    adapters: tuple[str, ...] | list[str] = ("spark", "hive", "impala"),
    *,
    catalog: list[dict[str, Any]] | None = None,
    timeout: float = 120.0,
) -> list[Any]:
    """LangChain StructuredTools bound to gateway MCP. Discovers the live catalog by default."""
    from langchain_core.tools import StructuredTool

    skipped: list[str] = []
    specs = catalog if catalog is not None else list_gateway_tools(adapters, skipped=skipped)
    if skipped:
        print("skipped disabled adapters:", ", ".join(skipped))
    tools: list[Any] = []
    for spec in specs:
        name = str(spec.get("name") or "").strip()
        if not name:
            continue
        adapter = str(spec.get("adapter") or adapter_for_tool(name))
        description = str(spec.get("description") or name)
        schema = spec.get("inputSchema") if isinstance(spec.get("inputSchema"), dict) else {"type": "object"}

        def _make_run(
            _name: str,
            _adapter: str,
            _schema: dict[str, Any],
            _timeout: float,
            _description: str,
        ):
            def _run(**kwargs: Any) -> str:
                return _invoke_mcp(_name, _adapter, _schema, _timeout, **kwargs)

            _run.__name__ = _name
            _run.__doc__ = _description
            return _run

        tools.append(
            StructuredTool.from_function(
                func=_make_run(name, adapter, schema, timeout, description),
                name=name,
                description=description,
                args_schema=schema_model(name, schema),
            )
        )
    return tools


def apply_model_settings(*, url: str = "", model_id: str = "", token: str = "") -> dict[str, Any]:
    """Store OpenAI-compatible endpoint settings for this engine only. Never returns the token."""
    cleaned_url = (url or "").strip().rstrip("/")
    cleaned_id = (model_id or "").strip()
    cleaned_token = (token or "").strip()
    if cleaned_url:
        os.environ["MODEL_URL"] = cleaned_url
    if cleaned_id:
        os.environ["MODEL_ID"] = cleaned_id
        os.environ["LANGGRAPH_MODEL"] = cleaned_id
    if cleaned_token:
        os.environ["MODEL_TOKEN"] = cleaned_token
    return resolved_model_endpoint()


def resolved_model_endpoint() -> dict[str, Any]:
    """url, model_id, token_set. Never includes the model token."""
    url = (
        os.environ.get("MODEL_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("OPENAI_API_BASE")
        or ""
    ).strip().rstrip("/")
    model_id = (
        os.environ.get("MODEL_ID") or os.environ.get("LANGGRAPH_MODEL") or os.environ.get("OPENAI_MODEL") or ""
    ).strip()
    token = (os.environ.get("MODEL_TOKEN") or os.environ.get("OPENAI_API_KEY") or "").strip()
    return {"url": url, "model_id": model_id, "token_set": bool(token)}


def show_model_form() -> dict[str, Any] | None:
    """IPython widgets for model URL, id, and token. None if ipywidgets is unavailable."""
    try:
        import ipywidgets as widgets
        from IPython.display import display
    except ImportError:
        return None
    layout = widgets.Layout(width="90%")
    style = {"description_width": "110px"}
    current = resolved_model_endpoint()
    url = widgets.Text(
        value=current["url"],
        description="Model URL",
        placeholder="https://host/v1",
        layout=layout,
        style=style,
    )
    model_id = widgets.Text(
        value=current["model_id"],
        description="Model ID",
        placeholder="served model name",
        layout=layout,
        style=style,
    )
    token = widgets.Password(
        description="Model token",
        placeholder="session only, not echoed",
        layout=layout,
        style=style,
    )
    save = widgets.Button(description="Save for this session", button_style="primary")
    status = widgets.HTML(value="<i>Fill the form, click Save, or run the next cell.</i>")

    def _on_save(_button=None) -> None:
        apply_model_settings(url=url.value, model_id=model_id.value, token=token.value)
        saved = resolved_model_endpoint()
        status.value = (
            f"<b>saved</b> url={saved['url'] or '(missing)'} "
            f"id={saved['model_id'] or '(missing)'} "
            f"token={'set' if saved['token_set'] else 'missing'}"
        )

    save.on_click(_on_save)
    display(widgets.VBox([url, model_id, token, save, status]))
    return {"url": url, "model_id": model_id, "token": token, "save": save, "status": status}


def apply_model_form(form: dict[str, Any] | None = None) -> dict[str, Any]:
    """Copy widget values into the engine env, then require a usable endpoint."""
    if form:
        apply_model_settings(
            url=str(getattr(form.get("url"), "value", "") or ""),
            model_id=str(getattr(form.get("model_id"), "value", "") or ""),
            token=str(getattr(form.get("token"), "value", "") or ""),
        )
    status = resolved_model_endpoint()
    has_cloud = bool(
        (os.environ.get("OPENAI_API_KEY") or "").strip() or (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    )
    if status["url"] and status["model_id"] and status["token_set"]:
        status["hint"] = "ok"
        return status
    if has_cloud and not status["url"]:
        status["hint"] = "using OPENAI_API_KEY or ANTHROPIC_API_KEY"
        return status
    raise RuntimeError(
        "Fill Model URL, Model ID, and Model token in the form (this session only). "
        "Do not print the token or put it in AMP project env. The Knox JWT is separate."
    )


def _should_omit_auto_tool_choice(base_url: str | None = None) -> bool:
    """vLLM rejects tool_choice=auto unless started with --enable-auto-tool-choice."""
    choice = (os.environ.get("MODEL_TOOL_CHOICE") or "").strip().lower()
    if choice == "auto":
        return False
    if choice in {"omit", "off"}:
        return True
    url = (
        (base_url or "").strip()
        or os.environ.get("MODEL_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("OPENAI_API_BASE")
        or ""
    ).strip()
    return bool(url)


def _drop_auto_tool_choice(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("tool_choice") == "auto":
        payload = dict(payload)
        payload.pop("tool_choice", None)
    return payload


def _set_llm_method(llm: Any, name: str, func: Any) -> None:
    """Bind a method on ChatOpenAI without Pydantic rejecting extra fields."""
    import types

    method = types.MethodType(func, llm)
    try:
        object.__setattr__(llm, name, method)
    except (TypeError, ValueError, AttributeError):
        setattr(llm, name, method)


def _omit_vllm_auto_tool_choice(llm: Any) -> Any:
    """Keep `tools` on the request but do not send tool_choice=auto (vLLM 400)."""
    orig_bind = llm.bind_tools

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # noqa: ANN001
        if _should_omit_auto_tool_choice() and tool_choice in {None, "auto"}:
            kwargs = {key: value for key, value in kwargs.items() if key != "tool_choice"}
            try:
                return orig_bind(tools, tool_choice=None, **kwargs)
            except TypeError:
                return orig_bind(tools, **kwargs)
        return orig_bind(tools, tool_choice=tool_choice, **kwargs)

    _set_llm_method(llm, "bind_tools", bind_tools)

    if hasattr(llm, "_get_request_payload"):
        orig_payload = llm._get_request_payload

        def _get_request_payload(self, *args, **kwargs):  # noqa: ANN001
            payload = orig_payload(*args, **kwargs)
            if isinstance(payload, dict) and _should_omit_auto_tool_choice():
                return _drop_auto_tool_choice(payload)
            return payload

        _set_llm_method(llm, "_get_request_payload", _get_request_payload)

    orig_generate = llm._generate

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001
        if _should_omit_auto_tool_choice() and kwargs.get("tool_choice") == "auto":
            kwargs = {key: value for key, value in kwargs.items() if key != "tool_choice"}
        try:
            return orig_generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        except TypeError:
            return orig_generate(messages, stop=stop, **kwargs)

    _set_llm_method(llm, "_generate", _generate)

    client = getattr(llm, "client", None)
    orig_create = getattr(client, "create", None) if client is not None else None
    if orig_create is not None:

        def create(*args, **kwargs):  # noqa: ANN001
            if _should_omit_auto_tool_choice() and kwargs.get("tool_choice") == "auto":
                kwargs = {key: value for key, value in kwargs.items() if key != "tool_choice"}
            return orig_create(*args, **kwargs)

        try:
            object.__setattr__(client, "create", create)
        except (TypeError, ValueError, AttributeError):
            try:
                client.create = create
            except (TypeError, ValueError, AttributeError):
                pass
    return llm


_TOOL_JSON_KEYS = ("name", "tool")


def _tool_names(tools: list[Any]) -> set[str]:
    names: set[str] = set()
    for tool in tools:
        name = getattr(tool, "name", None)
        if not name and isinstance(tool, dict):
            name = tool.get("name") or (tool.get("function") or {}).get("name")
        if name:
            names.add(str(name))
    return names


def _tool_brief(tool: Any) -> str:
    name = getattr(tool, "name", None) or (tool.get("name") if isinstance(tool, dict) else "") or ""
    desc = (getattr(tool, "description", None) or "").strip().split("\n", 1)[0]
    schema = getattr(tool, "args_schema", None)
    keys: list[str] = []
    if schema is not None:
        try:
            dumped = schema.model_json_schema() if hasattr(schema, "model_json_schema") else schema.schema()
            keys = list((dumped.get("properties") or {}).keys())
        except Exception:  # noqa: BLE001
            keys = []
    args = f" args={','.join(keys)}" if keys else ""
    return f"- {name}{args}: {desc}".strip()


def _tool_catalog_text(tools: list[Any]) -> str:
    lines = [
        "Call at most one MCP tool per turn. Reply with JSON only:",
        '{"name": "<tool>", "arguments": {<object>}}',
        "If you can answer without a tool, reply in plain text (no JSON object).",
        "Tools:",
    ]
    lines.extend(_tool_brief(tool) for tool in tools)
    return "\n".join(lines)


def _as_messages(value: Any) -> list[Any]:
    if isinstance(value, dict) and value.get("messages") is not None:
        return list(value["messages"])
    if isinstance(value, list):
        return list(value)
    return [value]


def _json_objects(text: str) -> list[dict[str, Any]]:
    blobs: list[str] = []
    stripped = (text or "").strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        blobs.append(stripped)
    blobs.extend(re.findall(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", text or "", flags=re.DOTALL))
    blobs.extend(re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text or "", flags=re.DOTALL))
    start = (text or "").find("{")
    end = (text or "").rfind("}")
    if start >= 0 and end > start:
        blobs.append((text or "")[start : end + 1])
    found: list[dict[str, Any]] = []
    for blob in blobs:
        try:
            obj = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            found.append(obj)
    return found


def parse_prompt_tool_call(text: str, tools: list[Any]) -> dict[str, Any] | None:
    """Parse a model reply into one {name, args} tool call. Never logs secrets."""
    allowed = _tool_names(tools)
    for obj in _json_objects(text):
        name = None
        for key in _TOOL_JSON_KEYS:
            raw = obj.get(key)
            if isinstance(raw, str) and raw.strip():
                name = raw.strip()
                break
        if not name or (allowed and name not in allowed):
            continue
        args = obj.get("arguments") if "arguments" in obj else obj.get("args", obj.get("parameters"))
        if not isinstance(args, dict):
            args = {}
        return {"name": name, "args": args}
    return None


def _attach_parsed_tool_calls(ai: Any, tools: list[Any]) -> Any:
    if getattr(ai, "tool_calls", None):
        return ai
    content = getattr(ai, "content", ai)
    if not isinstance(content, str):
        return ai
    parsed = parse_prompt_tool_call(content, tools)
    if not parsed:
        return ai
    from langchain_core.messages import AIMessage

    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": parsed["name"],
                "args": parsed["args"],
                "id": f"call_{uuid.uuid4().hex[:8]}",
                "type": "tool_call",
            }
        ],
    )


def _with_tool_catalog(messages: list[Any], tools: list[Any]) -> list[Any]:
    if not tools:
        return messages
    from langchain_core.messages import SystemMessage

    catalog = SystemMessage(content=_tool_catalog_text(tools))
    if messages and getattr(messages[0], "type", None) == "system":
        first = messages[0]
        content = f"{getattr(first, 'content', '')}\n\n{catalog.content}".strip()
        return [SystemMessage(content=content), *messages[1:]]
    return [catalog, *messages]


def _prompt_tool_chat(llm: Any, tools: list[Any] | None = None) -> Any:
    """Chat wrapper: LangGraph bind_tools without sending OpenAI `tools` (vLLM auto default)."""
    from langchain_core.runnables import Runnable

    class PromptToolChat(Runnable):
        def __init__(self, inner: Any, bound_tools: list[Any] | None = None) -> None:
            super().__init__()
            self.llm = inner
            self.tools = list(bound_tools or [])
            self._agentgateway_prompt_tools = True

        def bind_tools(self, next_tools, *, tool_choice=None, **kwargs):  # noqa: ANN001
            return PromptToolChat(self.llm, list(next_tools))

        def invoke(self, input, config=None, **kwargs):  # noqa: ANN001
            kwargs = {key: value for key, value in kwargs.items() if key not in {"tools", "tool_choice"}}
            messages = _with_tool_catalog(_as_messages(input), self.tools)
            ai = self.llm.invoke(messages, config=config, **kwargs)
            return _attach_parsed_tool_calls(ai, self.tools)

        async def ainvoke(self, input, config=None, **kwargs):  # noqa: ANN001
            kwargs = {key: value for key, value in kwargs.items() if key not in {"tools", "tool_choice"}}
            messages = _with_tool_catalog(_as_messages(input), self.tools)
            inner = getattr(self.llm, "ainvoke", None)
            if inner is None:
                ai = self.llm.invoke(messages, config=config, **kwargs)
            else:
                ai = await inner(messages, config=config, **kwargs)
            return _attach_parsed_tool_calls(ai, self.tools)

    wrapped = PromptToolChat(llm, tools)
    wrapped._agentgateway_prompt_tools = True
    return wrapped


def _make_chat_openai(*, model: str, api_key: str, base_url: str | None, temperature: float) -> Any:
    from langchain_openai import ChatOpenAI

    params = inspect.signature(ChatOpenAI).parameters
    kwargs: dict[str, Any] = {"temperature": temperature}
    if "model" in params:
        kwargs["model"] = model
    else:
        kwargs["model_name"] = model
    if "api_key" in params:
        kwargs["api_key"] = api_key
    else:
        kwargs["openai_api_key"] = api_key
    if base_url:
        if "base_url" in params:
            kwargs["base_url"] = base_url
        else:
            kwargs["openai_api_base"] = base_url
    llm = ChatOpenAI(**kwargs)
    if _should_omit_auto_tool_choice(base_url):
        llm = _omit_vllm_auto_tool_choice(llm)
        return _prompt_tool_chat(llm)
    return llm


def chat_model(*, temperature: float = 0) -> Any:
    """OpenAI-compatible endpoint from the notebook form, else OpenAI/Anthropic cloud keys."""
    url = (
        os.environ.get("MODEL_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("OPENAI_API_BASE")
        or ""
    ).strip().rstrip("/")
    token = (os.environ.get("MODEL_TOKEN") or os.environ.get("OPENAI_API_KEY") or "").strip()
    model_id = (
        os.environ.get("MODEL_ID") or os.environ.get("LANGGRAPH_MODEL") or os.environ.get("OPENAI_MODEL") or ""
    ).strip()
    if url:
        if not token or not model_id:
            raise RuntimeError(
                "Model URL is set; also set Model ID and Model token in the form (session only)."
            )
        return _make_chat_openai(model=model_id, api_key=token, base_url=url, temperature=temperature)
    if token:
        return _make_chat_openai(
            model=model_id or "gpt-4o-mini",
            api_key=token,
            base_url=None,
            temperature=temperature,
        )
    if (os.environ.get("ANTHROPIC_API_KEY") or "").strip():
        from langchain_anthropic import ChatAnthropic

        model = (os.environ.get("LANGGRAPH_MODEL") or os.environ.get("ANTHROPIC_MODEL") or "claude-sonnet-4-5").strip()
        return ChatAnthropic(model=model, temperature=temperature)
    raise RuntimeError(
        "Fill the model form (URL, id, token) for this engine, or set OPENAI_API_KEY / "
        "ANTHROPIC_API_KEY. Do not commit model keys or put them in AMP project env. "
        "The Knox JWT is separate (getpass / KNOX_TOKEN)."
    )


def _ignore_langgraph_pending_warnings() -> None:
    """LangGraph 0.2 imports JsonPlusSerializer with a pending allowed_objects default."""
    import warnings

    warnings.filterwarnings("ignore", message=r".*allowed_objects.*")
    try:
        from langchain_core._api.deprecation import LangChainPendingDeprecationWarning
    except ImportError:
        LangChainPendingDeprecationWarning = None  # type: ignore[misc, assignment]
    if LangChainPendingDeprecationWarning is not None:
        warnings.filterwarnings("ignore", category=LangChainPendingDeprecationWarning, message=r".*allowed_objects.*")


def make_agent(
    model: Any | None = None,
    *,
    tools: list[Any] | None = None,
    system: str = SYSTEM_PROMPT,
) -> Any:
    """create_react_agent over gateway MCP tools. Prompt kwarg name varies by LangGraph version."""
    _ignore_langgraph_pending_warnings()
    from langgraph.prebuilt import create_react_agent

    bound = tools if tools is not None else langchain_tools()
    llm = model if model is not None else chat_model()
    if _should_omit_auto_tool_choice() and not getattr(llm, "_agentgateway_prompt_tools", False):
        llm = _prompt_tool_chat(llm)
    params = inspect.signature(create_react_agent).parameters
    kwargs: dict[str, Any] = {}
    if "prompt" in params:
        kwargs["prompt"] = system
    elif "state_modifier" in params:
        kwargs["state_modifier"] = system
    return create_react_agent(llm, bound, **kwargs)


def last_ai_text(result: dict[str, Any]) -> str:
    """Last AI message text. Does not dump tool payloads or headers."""
    messages = result.get("messages") or []
    for message in reversed(messages):
        if getattr(message, "type", None) != "ai":
            continue
        content = getattr(message, "content", "")
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
            text = "".join(parts).strip()
            if text:
                return text
    return ""


def tool_names_used(result: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for message in result.get("messages") or []:
        for call in getattr(message, "tool_calls", None) or []:
            if isinstance(call, dict):
                name = call.get("name")
            else:
                name = getattr(call, "name", None)
            if name:
                names.append(str(name))
        if getattr(message, "type", None) == "tool":
            name = getattr(message, "name", None)
            if name:
                names.append(str(name))
    return names


def invoke_agent(
    question: str,
    *,
    agent: Any | None = None,
    recursion_limit: int | None = None,
) -> dict[str, Any]:
    """Run one user turn. Loads the Knox JWT first so MCP calls have Authorization."""
    load_knox_token(prompt=True)
    graph = agent if agent is not None else make_agent()
    limit = recursion_limit
    if limit is None:
        limit = int(os.environ.get("LANGGRAPH_RECURSION_LIMIT", "40"))
    try:
        return graph.invoke(
            {"messages": [("user", question)]},
            config={"recursion_limit": limit},
        )
    except Exception as exc:
        text = str(exc)
        if "enable-auto-tool-choice" in text or (
            "tool choice" in text.lower() and "auto" in text.lower()
        ):
            raise RuntimeError(
                "The model server rejected tool_choice=auto. vLLM treats OpenAI `tools` "
                "without tool_choice as auto, so custom MODEL_URL now uses prompt-parsed "
                "tool calls (no `tools` in the HTTP body). Re-run make_agent after pulling "
                "that helper. If this vLLM has --enable-auto-tool-choice, set "
                "MODEL_TOOL_CHOICE=auto for native OpenAI tools."
            ) from exc
        raise
