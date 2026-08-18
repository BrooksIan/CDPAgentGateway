# Third-party agent demo

[`third_party_agent.ipynb`](third_party_agent.ipynb) simulates an external MCP host (Cursor, Claude, custom agent) calling CDP Agent Gateway. It uses POST JSON-RPC only; it never talks to Knox or Livy directly.

## Prerequisites

- **Knox JWT** pasted in the notebook token cell (`getpass`, session only). Optional: `KNOX_TOKEN` for this engine, or `KNOX_TOKEN_FILE`. Never commit tokens, never add them to AMP project env, never print them.
- **Compose:** `gateway up` on `http://127.0.0.1:9080`. MCP routes need `X-Agent-Key` (default `lab-agent`).
- **AMP:** `agent-gateway` plus Spark/Hive/Impala MCP applications. Knox JWT as `Authorization: Bearer`.

## URLs

| Profile | Spark MCP |
| --- | --- |
| Compose | `http://127.0.0.1:9080/mcp/spark` |
| AMP | `https://agent-gateway.<CDSW_DOMAIN>/mcp/spark` (APISIX; preferred) |

Override with `MCP_SPARK_URL`, `MCP_HIVE_URL`, or `MCP_IMPALA_URL` if your workspace uses a different hostname pattern.

## Run

1. Open the notebook in Workbench (Python 3.11+) or Jupyter locally.
2. Run cells in order. The **Knox JWT** cell prompts with `getpass` (not echoed). Then health, `tools/list`, Spark → Hive.

On AMP, stage the job on HDFS first (or set `SPARK_FILE_URI`). Spark jobs can take several minutes; override wait with `SPARK_POLL_TIMEOUT` (seconds).

Helper module: [`mcp_agent.py`](mcp_agent.py) (same logic, importable from other demos).
