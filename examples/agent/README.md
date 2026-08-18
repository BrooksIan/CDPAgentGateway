# Third-party agent demo

These notebooks **demonstrate third-party agents** against CDP Agent Gateway. They stand in for Cursor, Claude, or a partner SDK: POST JSON-RPC to `/mcp/spark`, `/mcp/hive`, and `/mcp/impala` with a Knox JWT. They never talk to Knox, Livy, HiveServer2, or Impala directly.

| Notebook | What it demonstrates |
| --- | --- |
| [`third_party_agent.ipynb`](third_party_agent.ipynb) | Scripted third-party MCP host: health, `tools/list`, Spark → Hive |
| [`langgraph_agent.ipynb`](langgraph_agent.ipynb) | **LangGraph** ReAct agent bound to the same MCP tools |

## Prerequisites

- **Knox JWT** pasted in the notebook token cell (`getpass`, session only). It must be a Token API **JWT** (`eyJ…`, three segments, `alg=RS256`, `iss=KNOXSSO`). A Knox passcode, cookie, or `Bearer eyJ…` paste becomes `401 invalid_token`. Optional: `KNOX_TOKEN` for this engine, or `KNOX_TOKEN_FILE`. Never commit tokens, never add them to AMP project env, never print them.
- **Compose:** `gateway up` on `http://127.0.0.1:9080`. MCP routes need `X-Agent-Key` (default `lab-agent`).
- **AMP:** `agent-gateway` plus Spark/Hive/Impala MCP applications. Knox JWT as `Authorization: Bearer`.
- **LangGraph notebook only:** a form for **Model URL**, **Model ID**, and **Model token** (OpenAI-compatible endpoint, this engine only). Optional cloud keys: `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`. The notebook installs LangGraph to match CML: `langchain` 0.2.x → core 0.2 / LangGraph 0.2; `langchain` 0.3.x → core 0.3 / LangGraph 0.3. Do not install langchain-core 1.x on AMP. Do not commit model keys.

## URLs

| Profile | Spark MCP |
| --- | --- |
| Compose | `http://127.0.0.1:9080/mcp/spark` |
| AMP | `https://cdp-ag.<CDSW_DOMAIN>/mcp/spark` (APISIX; preferred) |

Override with `MCP_SPARK_URL`, `MCP_HIVE_URL`, or `MCP_IMPALA_URL` if your workspace uses a different hostname pattern.

## Run

1. Open a notebook in Workbench (Python 3.11+) or Jupyter locally.
2. Run cells in order. The **Knox JWT** cell prompts with `getpass` (not echoed).
3. Scripted notebook: health, `tools/list`, Spark → Hive. Re-running submit **reuses** an existing `count-to-10` Livy batch (`SPARK_FORCE_SUBMIT=1` to submit again). The job file reuses Livy's SparkSession (restage with `gateway webhdfs put` after pulling this change).
4. LangGraph notebook: Knox JWT, **model form** (URL / id / token), bind MCP tools, then a read-only ReAct turn. Custom model URLs use **prompt-parsed** tool calls (vLLM defaults omitted `tool_choice` to `auto` when `tools` are present). AMP pip warnings about CML `protobuf` / `typing-extensions` pins can be ignored; the notebook does not change protobuf. If this engine has `langchain` 0.2.x, the install cell keeps `langchain-core` 0.2 (do not leave a previous 0.3 install in the kernel — restart the session). Set `LANGGRAPH_RUN_SUBMIT=1` only if the job file is already staged.

On AMP, stage the job on HDFS first (or set `SPARK_FILE_URI`). Spark jobs can take several minutes; override wait with `SPARK_POLL_TIMEOUT` (seconds). If a previous notebook cell installed langchain-core 1.x, **restart the Workbench session** and re-run.

This sample does **not** use Streamable HTTP or `langchain-mcp-adapters` transports. Tools are LangChain wrappers around [`mcp_agent.py`](mcp_agent.py) `tools/list` / `tools/call`. LangGraph helper: [`langgraph_mcp.py`](langgraph_mcp.py).
