# Build phases

Each phase keeps CDP APIs unpublished except through this gateway. **Current target: Phase 2 Spark MCP + read-only Hive MCP** on the Phase 1 Livy allowlist. `spark_submit_batch` is a write as the Knox token subject. Hive MCP does not write.

## Phase 0 — Inventory and test cases

Confirm Private Cloud Base vs Public Cloud, Knox homepage JWKS, token TTL, and whether impersonation is enabled. The first tool is Livy for Spark 3 session list. Write the threat model.

Deliverables:

- Human worksheet: [phase-0-inventory.md](phase-0-inventory.md)
- Machine inventory: `inventory/cdp.yaml` (pytest asserts schema)
- Test cases in [testing.md](testing.md)
- `.env` from `gateway init` / `gateway knox` / `gateway jdbc add` (not committed)

Status: schema and local tests exist. Fill `gateway_url` / `jwks_url` in `inventory/cdp.yaml` before treating a live cluster as inventoried.

## Phase 1 — HTTP perimeter (local Docker → external CDP)

Laptop APISIX in front of mock Knox or a reachable `cdp-proxy-token` URL.

- Operator CLI: [operator-cli.md](operator-cli.md)
- Spark how-to: [spark.md](spark.md)
- Hive inventory: [hive.md](hive.md)
- Knox JWT verification via `plugins/knox-jwt.lua` and a pinned PEM
- Proxy Livy for Spark 3: `GET`/`HEAD` `/cdp/livy_for_spark3*` → `{KNOX_PROXY_PREFIX}/livy_for_spark3/...`. Writes are not on this route.
- Proxy WebHDFS: `GET`/`HEAD`/`PUT` `/cdp/webhdfs*` → `{KNOX_PROXY_PREFIX}/webhdfs/...`. `DELETE` is not a route. Operator CLI: `gateway webhdfs`.
- `X-Request-Id` on every route; `X-Knox-User` / `X-Knox-Token-Id` on authenticated Spark calls
- Lab: `gateway test`. Live: `gateway knox <url>`, `gateway token set`, `gateway hive`, `gateway test --live`

Success: a client can list Livy Spark 3 sessions with a Knox JWT through local APISIX, every call is audited, Hive and other CDP paths 404, and the same token against the raw Knox URL from an untrusted network remains blocked at the CDP perimeter.

Status: local mock path is implemented. Live path is the same Compose file plus `.env`. TLS on the local listener is still HTTP `:9080`.

## Phase 2 — Agent protocol

`mcp-spark` is a Compose upstream. APISIX validates the Knox JWT on `/mcp/spark` and forwards `Authorization`. `mcp-hive` does the same on `/mcp/hive` with read-only Hive tools. Raw Livy on the agent listener is GET/HEAD only. WebHDFS on the agent listener is GET/HEAD/PUT for operator staging. `/cdp/hive` stays 404.

Tools: Spark `spark_list_sessions`, `spark_list_batches`, `spark_get_batch`, `spark_get_log`, `spark_submit_batch` (HDFS/object-store `file` only). Hive `hive_list_databases`, `hive_list_tables`, `hive_describe_table`, `hive_select` (named columns, limit ≤ 50). Logs are truncated. Interactive Livy `run code` is not exposed.

Status: Spark MCP catalog is implemented, including submit (2b). Hive MCP is read-only (P2-05). Livy HTTP writes are closed on the agent address (2a). Operator admin UI records usage, joins tool/`sub`/`knox.id` by `X-Request-Id` (P2-04), and enforces per-`sub` quotas. `/mcp/spark` and `/mcp/hive` have an APISIX `limit-count` burst cap keyed by Knox `sub` (P2-13). The MCP contract is **POST JSON-RPC**; Streamable HTTP is held. Partner mTLS remains.

## Phase 3 — Third-party ready

Publish `/.well-known/oauth-protected-resource` and richer `401 WWW-Authenticate`. Broker authorization to Knox or an enterprise IdP that can exchange into a Knox JWT. Require mTLS or caller keys for partner agents. Check Knox token enablement/revocation. Cap result size and redact PII before data returns to a model.

Status: not started.

## Suggested repo slices

| Slice | Contents | Depends on |
| --- | --- | --- |
| `deploy/` + `conf/` + `plugins/` | APISIX routes, Knox JWT plugin, Compose | Knox JWKS URL |
| `src/agentgateway/` | Operator CLI | Phase 1 proxy |
| `mcp-spark` | Livy / Spark tools over Knox | Phase 1 Spark proxy | [spark.md](spark.md) |
| `admin/` | Operator UI + sqlite usage/quotas | Phase 2 Spark MCP | [admin.md](admin.md) |
| AMP (`.project-metadata.yaml`) | Optional CML apps; Python JWT; live Knox | Phase 2 Spark MCP | [amp.md](amp.md) |
| `mcp-hive` | Read-only SQL tools over Knox Hive | `mcp-spark` + JDBC inventory | [hive.md](hive.md) |
| `mcp-catalog` | Atlas / schema discovery tools | `mcp-hive` |
| `policy` | Tool allowlists, row caps | Identity model (admin quotas + request_id audit join) |
| `oauth-adapter` | PRM, PKCE broker, token exchange to Knox | IdP decision |

## Open decisions

| Decision | Default | Why it waits |
| --- | --- | --- |
| Private Cloud vs CDP Public Cloud | `gateway knox` parses both `/gateway/cdp-proxy-token` and `/<env>/cdp-proxy-token` | Confirm JWKS path on the target cluster |
| HTTPS on localhost APISIX | HTTP `:9080` for the laptop lab | Add TLS before partner agents leave the VPN |
| IdP in front of Knox | None in Phase 1 | Needed when MCP OAuth onboarding starts |
| Streamable HTTP on `/mcp/spark` | POST JSON-RPC only | Hold until a real host fails `initialize`; do not add GET SSE now |
| AMP `launchable: true` | false | Needs a workbench import against live Knox recorded in [testing.md](testing.md) |
| Revocation check vs short TTL | Short TTL first | Knox token-state API coupling vs leak window |
