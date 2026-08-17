# Third-party agent demo

[`third_party_agent.ipynb`](third_party_agent.ipynb) simulates an external MCP host (Cursor, Claude, custom agent) calling CDP Agent Gateway. It uses POST JSON-RPC only; it never talks to Knox or Livy directly.

## Prerequisites

- **Knox JWT** in `KNOX_TOKEN` (project environment on CML; local secret store or `.env` on Compose). Never commit tokens or paste them into git.
- **Compose:** `gateway up` on `http://127.0.0.1:9080`. MCP routes need `X-Agent-Key` (default `lab-agent`).
- **AMP:** Spark, Hive, and Impala MCP applications running. JWT only; no caller key.

## URLs

| Profile | Spark MCP |
| --- | --- |
| Compose | `http://127.0.0.1:9080/mcp/spark` |
| AMP | `https://mcp-spark.<CDSW_DOMAIN>/mcp/spark` |

Override with `MCP_SPARK_URL`, `MCP_HIVE_URL`, or `MCP_IMPALA_URL` if your workspace uses a different hostname pattern.

## Run

1. Open the notebook in Workbench (Python 3.11+) or Jupyter locally.
2. Set `KNOX_TOKEN` in project settings (CML) or export it in your shell (Compose).
3. Run all cells. After health and `tools/list`, the notebook runs the Spark → Hive example: stage `count_to_10.py` (Compose WebHDFS only), `spark_submit_batch`, poll `spark_get_batch`, then `hive_select` `{sub}.count_to_10`.

On AMP, stage the job on HDFS first (or set `SPARK_FILE_URI`). Spark jobs can take several minutes; override wait with `SPARK_POLL_TIMEOUT` (seconds).

Helper module: [`mcp_agent.py`](mcp_agent.py) (same logic, importable from other demos).
