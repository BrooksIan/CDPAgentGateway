# Agent instructions

This repo is a **CDP agent gateway**: Apache APISIX in front of Apache Knox so third-party agents can use Cloudera Data Platform without talking to cluster APIs directly.

This repo follows the [Cloudera Blueprints Standard](https://github.com/kevinbtalbert/Cloudera-Blueprints-Standard): business onboarding stays in `README.md`; catalog fields stay in `METADATA.yaml`. Do not remove required README sections or metadata keys.

Read [docs/architecture.md](docs/architecture.md) and [docs/identity-and-auth.md](docs/identity-and-auth.md) before changing behavior.

## Hard constraints

- Keep Knox as the CDP perimeter. Do not replace it with AISIX, Kong, or a custom auth service.
- Validate Knox-issued RS256 JWTs. Do not use APISIX `jwt-auth` as the primary authenticator (it mints APISIX tokens).
- Bind two identities on every request: agent consumer (mTLS or caller key) and Knox `sub`.
- Ranger remains authorization. Never impersonate a different user than the token `sub`.
- MCP adapters are upstream services, not APISIX plugins and not the experimental `mcp-bridge`.
- Keep `/mcp/spark` as POST JSON-RPC. Do not add Streamable HTTP (GET SSE / session) unless a real host fails `initialize` and the operator asks for it.
- Never commit `.env`, Knox tokens, passcodes, keytabs, or JWKS private material.

## How to work

1. Fill [docs/phase-0-inventory.md](docs/phase-0-inventory.md) and `inventory/cdp.yaml` for the external CDP under test.
2. Put secrets only in `.env` (from `.env.example`).
3. Change Compose and APISIX config under `deploy/`, `conf/`, and `plugins/`.
4. Use the operator CLI for local work: `gateway init`, `gateway knox <knox-proxy-url>`, `gateway jdbc add <jdbc:hive2://…>`, `gateway up`, `gateway test`. `python -m agentgateway` is the same entry point.
5. Execute cases in [docs/testing.md](docs/testing.md); record results without tokens.
6. Keep `README.md` catalog sections and `METADATA.yaml` in sync when the product story changes.

Current target: **Phase 2 Spark MCP** on the Phase 1 Livy allowlist (`spark_submit_batch` is a write as the Knox subject).

## Runtime agents (Cursor, Claude)

Call `http://127.0.0.1:9080/mcp/spark` with the **user's Knox JWT** as `Authorization: Bearer`. How-to: [docs/spark.md](docs/spark.md). Hive is inventory-only: [docs/hive.md](docs/hive.md). Do not call Knox, Hive, or raw Livy writes. `/cdp/livy_for_spark3*` is GET/HEAD only. Do not log or echo the bearer.

Tools: `spark_list_sessions`, `spark_list_batches`, `spark_get_batch`, `spark_get_log`, `spark_submit_batch`. Submit `examples/spark/count_to_10.py` after copying it to HDFS/Ozone. Poll with `spark_get_batch`. Do not run interactive Livy `code`.

Operator usage, quotas, and audit join: [docs/admin.md](docs/admin.md) (`http://127.0.0.1:9090`). Do not send agents there. `/mcp/spark` is burst-capped per Knox `sub` (`MCP_RATE_COUNT`).

Example MCP host config (put the JWT in the host's secret store, never in git):

```json
{
  "mcpServers": {
    "cdp-spark": {
      "url": "http://127.0.0.1:9080/mcp/spark",
      "headers": {
        "Authorization": "Bearer <knox-jwt>"
      }
    }
  }
}
```

## Code and config style

- Prefer declarative APISIX config and Compose over one-off Admin API scripts.
- Pin the Knox JWKS host; never follow an arbitrary `jku`.
- Log `sub` and `knox.id`, never the raw bearer.
- Keep docs in `docs/` current when architecture, CLI, or test IDs change. Operator commands belong in [docs/operator-cli.md](docs/operator-cli.md).
