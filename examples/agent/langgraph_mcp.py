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
import subprocess
import sys
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from mcp_agent import (
    adapter_for_tool,
    call_gateway_tool,
    list_gateway_tools,
    load_knox_token,
)

# CML workbench ships langchain 0.3 / langchain-aws (langchain-core<0.4). LangGraph 1.x
# pulls langchain-core 1.x and breaks that runtime. Keep the 0.3 line.
LANGGRAPH_PIP_PACKAGES = (
    "langgraph>=0.3.5,<0.4",
    "langchain-core>=0.3.85,<0.4",
    "langchain-openai>=0.3,<0.4",
    "langchain-anthropic>=0.3,<0.4",
)
_CONSTRAINTS = Path(__file__).resolve().parent / "langgraph-constraints.txt"

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


def langgraph_pip_packages(*, amp: bool | None = None) -> list[str]:
    """Package specs that coexist with CML langchain 0.3 (langchain-core<0.4)."""
    packages = list(LANGGRAPH_PIP_PACKAGES)
    if amp is None:
        amp = _amp_runtime()
    if amp:
        core = _dist_version("langchain-core")
        if core and core.startswith("0.3."):
            packages = [item for item in packages if not item.startswith("langchain-core")]
        proto = _dist_version("protobuf")
        if proto:
            try:
                proto_major = int(proto.split(".", 1)[0])
            except ValueError:
                proto_major = 0
            if proto_major >= 6:
                packages.append("protobuf>=3.12.0,<6")
    return packages


def require_langchain_core_03() -> str:
    """Fail closed if this engine has langchain-core 1.x (breaks CML langchain 0.3)."""
    core = _dist_version("langchain-core")
    if not core:
        raise RuntimeError("langchain-core is not installed")
    major = int(core.split(".", 1)[0])
    if major >= 1:
        raise RuntimeError(
            f"langchain-core {core} is 1.x; Cloudera AI Workbench langchain 0.3 needs <0.4. "
            "Restart this session, then re-run the install cell "
            "(it pins langchain-core>=0.3.85,<0.4). Do not pip install langchain-core 1.x on AMP."
        )
    loaded = sys.modules.get("langchain_core")
    loaded_ver = getattr(loaded, "__version__", "") if loaded is not None else ""
    if loaded_ver.startswith("1."):
        raise RuntimeError(
            "This kernel still has langchain-core 1.x imported. Restart the kernel and re-run."
        )
    return core


def install_langgraph_deps(*, root: Path | None = None) -> list[str]:
    """Install the 0.3-line LangGraph stack. On AMP, do not reinstall the gateway extra."""
    amp = _amp_runtime()
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
    if _CONSTRAINTS.is_file():
        cmd.extend(["-c", str(_CONSTRAINTS)])
    cmd.extend(packages)
    print("langgraph pip:", " ".join(packages))
    subprocess.check_call(cmd, cwd=str(root or Path.cwd()))
    print("langchain-core:", require_langchain_core_03())
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

    specs = catalog if catalog is not None else list_gateway_tools(adapters)
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


def chat_model(*, temperature: float = 0) -> Any:
    """OpenAI when OPENAI_API_KEY is set, else Anthropic when ANTHROPIC_API_KEY is set."""
    if (os.environ.get("OPENAI_API_KEY") or "").strip():
        from langchain_openai import ChatOpenAI

        model = (os.environ.get("LANGGRAPH_MODEL") or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini").strip()
        return ChatOpenAI(model=model, temperature=temperature)
    if (os.environ.get("ANTHROPIC_API_KEY") or "").strip():
        from langchain_anthropic import ChatAnthropic

        model = (
            os.environ.get("LANGGRAPH_MODEL") or os.environ.get("ANTHROPIC_MODEL") or "claude-sonnet-4-5"
        ).strip()
        return ChatAnthropic(model=model, temperature=temperature)
    raise RuntimeError(
        "Set OPENAI_API_KEY or ANTHROPIC_API_KEY for this engine only. Do not commit model keys "
        "or put them in AMP project env. The Knox JWT is separate (getpass / KNOX_TOKEN)."
    )


def make_agent(
    model: Any | None = None,
    *,
    tools: list[Any] | None = None,
    system: str = SYSTEM_PROMPT,
) -> Any:
    """create_react_agent over gateway MCP tools. Prompt kwarg name varies by LangGraph version."""
    from langgraph.prebuilt import create_react_agent

    bound = tools if tools is not None else langchain_tools()
    llm = model if model is not None else chat_model()
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
    return graph.invoke(
        {"messages": [("user", question)]},
        config={"recursion_limit": limit},
    )
