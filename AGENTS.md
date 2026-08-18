# Agent instructions

This repo is a **CDP agent gateway**: Apache APISIX in front of Apache Knox so third-party agents can use Cloudera Data Platform without talking to cluster APIs directly.

This repo follows the [Cloudera Blueprints Standard](https://github.com/kevinbtalbert/Cloudera-Blueprints-Standard): business onboarding stays in `README.md`; catalog fields stay in `METADATA.yaml`. Do not remove required README sections or metadata keys.

Read [docs/architecture.md](docs/architecture.md) and [docs/identity-and-auth.md](docs/identity-and-auth.md) before changing behavior.

## Hard constraints

- Keep Knox as the CDP perimeter. Do not replace it with AISIX, Kong, or a custom auth service.
- Validate Knox-issued RS256 JWTs. Do not use APISIX `jwt-auth` as the primary authenticator (it mints APISIX tokens).
- Bind two identities on MCP: agent caller key (`X-Agent-Key`) and Knox `sub`. Operator Livy/WebHDFS are JWT-only. AMP agents use APISIX on `agent-gateway` (caller key when `AGENT_CALLER_KEY` is set).
- Ranger remains authorization. Never impersonate a different user than the token `sub`.
- MCP adapters are upstream services, not APISIX plugins and not the experimental `mcp-bridge`.
- Optional CML AMP packaging lives in `.project-metadata.yaml` and `docs/amp.md`. Do not flip `launchable: true` without a workbench proof. Do not run full Compose/mock-cdp inside CML; AMP uses Docker only for the APISIX edge app.
- Keep `/mcp/spark`, `/mcp/hive`, and `/mcp/impala` as POST JSON-RPC. Do not add Streamable HTTP (GET SSE / session) unless a real host fails `initialize` and the operator asks for it.
- Never commit `.env`, Knox tokens, passcodes, keytabs, or JWKS private material.

## How to work

1. Fill [docs/phase-0-inventory.md](docs/phase-0-inventory.md) and `inventory/cdp.yaml` for the external CDP under test.
2. Put secrets only in `.env` (from `.env.example`).
3. Change Compose and APISIX config under `deploy/`, `conf/`, and `plugins/`. AMP jobs/apps stay in numbered `0_`–`7_` dirs plus `src/agentgateway/amp.py` and `src/agentgateway/amp_apisix.py`.
4. Use the operator CLI for local work: `gateway init`, `gateway knox <knox-proxy-url>`, `gateway jdbc add <jdbc:hive2://… or jdbc:impala://…>`, `gateway webhdfs put`, `gateway up`, `gateway test`. `python -m agentgateway` is the same entry point.
5. Execute cases in [docs/testing.md](docs/testing.md); record results without tokens.
6. Keep `README.md` catalog sections and `METADATA.yaml` in sync when the product story changes.

Current target: **Phase 3 third-party ready** is implemented on Compose (RFC 9728 PRM, MCP `X-Agent-Key`, local Knox token-state, Spark log redaction). PKCE broker and mTLS wait. Spark `spark_submit_batch` is a write as the Knox subject. Hive and Impala tools stay list/describe/select only.

## Runtime agents (Cursor, Claude)

Call `http://127.0.0.1:9080/mcp/spark`, `http://127.0.0.1:9080/mcp/hive`, or `http://127.0.0.1:9080/mcp/impala` with the **user's Knox JWT** as `Authorization: Bearer` and Compose caller key `X-Agent-Key: lab-agent` (or `$AGENT_CALLER_KEY`). How-to: [docs/spark.md](docs/spark.md), [docs/hive.md](docs/hive.md), [docs/impala.md](docs/impala.md). Optional AMP URL: [docs/amp.md](docs/amp.md) (`https://agent-gateway.<workspace>/mcp/*` through APISIX). Do not call Knox or raw Livy writes. `/cdp/livy_for_spark3*` is GET/HEAD only. `/cdp/webhdfs*` is operator staging (`gateway webhdfs`), not an MCP tool. `/cdp/hive` and `/cdp/impala` are 404. Do not log or echo the bearer.

Spark tools: `spark_list_sessions`, `spark_list_batches`, `spark_get_batch`, `spark_get_log`, `spark_submit_batch`. Submit `examples/spark/count_to_10.py` after `gateway webhdfs put` (optional args: database, table). It writes Iceberg `{user}.count_to_10` (`n`). Poll with `spark_get_batch`. Do not run interactive Livy `code`.

Hive tools: `hive_list_databases`, `hive_list_tables`, `hive_describe_table`, `hive_select`. After Spark writes `{user}.count_to_10`, call `hive_select` with `database=$USER`, `table=count_to_10`, `columns=n`, `limit=10`. Named columns only (no `SELECT *`), `limit` ≤ 50. No DDL/DML.

Impala tools: `impala_list_databases`, `impala_list_tables`, `impala_describe_table`, `impala_select`. Same Iceberg table when Impala has HMS metadata; no `INVALIDATE METADATA` tool. Named columns only, `limit` ≤ 50. No DDL/DML.

Operator usage, quotas, and audit join: [docs/admin.md](docs/admin.md) (`http://127.0.0.1:9090`). Do not send agents there. `/mcp/spark`, `/mcp/hive`, and `/mcp/impala` are burst-capped per Knox `sub` (`MCP_RATE_COUNT`). Live Compose uses the user's Knox JWT; do not pass `--mint` (`GATEWAY_MODE=live` refuses it).

Example MCP host config (put the JWT in the host's secret store, never in git):

```json
{
  "mcpServers": {
    "cdp-spark": {
      "url": "http://127.0.0.1:9080/mcp/spark",
      "headers": {
        "Authorization": "Bearer <knox-jwt>",
        "X-Agent-Key": "lab-agent"
      }
    },
    "cdp-hive": {
      "url": "http://127.0.0.1:9080/mcp/hive",
      "headers": {
        "Authorization": "Bearer <knox-jwt>",
        "X-Agent-Key": "lab-agent"
      }
    },
    "cdp-impala": {
      "url": "http://127.0.0.1:9080/mcp/impala",
      "headers": {
        "Authorization": "Bearer <knox-jwt>",
        "X-Agent-Key": "lab-agent"
      }
    }
  }
}
```

Third-party agent demos (POST JSON-RPC only; do not add Streamable HTTP): [examples/agent/third_party_agent.ipynb](examples/agent/third_party_agent.ipynb) is a scripted MCP host; [examples/agent/langgraph_agent.ipynb](examples/agent/langgraph_agent.ipynb) shows LangGraph ReAct over the same tools. How-to: [examples/agent/README.md](examples/agent/README.md).

## Code and config style

- Prefer declarative APISIX config and Compose over one-off Admin API scripts.
- Pin the Knox JWKS host; never follow an arbitrary `jku`.
- Log `sub` and `knox.id`, never the raw bearer.
- Keep docs in `docs/` current when architecture, CLI, or test IDs change. Operator commands belong in [docs/operator-cli.md](docs/operator-cli.md).
