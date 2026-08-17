# Build phases

Each phase keeps CDP APIs unpublished except through this gateway. **Current target: Phase 3 third-party ready** — RFC 9728 PRM, MCP caller keys, Knox token-state, and log redaction are implemented on the Phase 2 Spark + Hive + Impala MCP. Held: PKCE broker (P3-05) and partner mTLS (P3-06).

## Phase 0 — Inventory and test cases

Confirm Private Cloud Base vs Public Cloud, Knox homepage JWKS, token TTL, and whether impersonation is enabled. The first tool is Livy for Spark 3 session list. Write the threat model.

Deliverables:

- Human worksheet: [phase-0-inventory.md](phase-0-inventory.md)
- Machine inventory: `inventory/cdp.yaml` (pytest asserts schema)
- Test cases in [testing.md](testing.md)
- `.env` from `gateway init` / `gateway knox` / `gateway jdbc add` (not committed)

Status: schema and local tests exist. Live Public Cloud `go01-obser-de` is inventoried (`gateway_url` / `jwks_url` in `inventory/cdp.yaml`). Worksheet: [phase-0-inventory.md](phase-0-inventory.md).

## Phase 1 — HTTP perimeter (local Docker → external CDP)

Laptop APISIX in front of mock Knox or a reachable `cdp-proxy-token` URL.

- Operator CLI: [operator-cli.md](operator-cli.md)
- Spark how-to: [spark.md](spark.md)
- Hive inventory and MCP: [hive.md](hive.md)
- Knox JWT verification via `plugins/knox-jwt.lua` and a pinned PEM
- Proxy Livy for Spark 3: `GET`/`HEAD` `/cdp/livy_for_spark3*` → `{KNOX_PROXY_PREFIX}/livy_for_spark3/...`. Writes are not on this route.
- Proxy WebHDFS: `GET`/`HEAD`/`PUT` `/cdp/webhdfs*` → `{KNOX_PROXY_PREFIX}/webhdfs/...`. `DELETE` is not a route. Operator CLI: `gateway webhdfs`.
- `X-Request-Id` on every route; `X-Knox-User` / `X-Knox-Token-Id` on authenticated Spark calls
- Lab: `gateway test`. Live: `gateway knox <url>`, `gateway token set`, `gateway hive`, `gateway test --live`

Success: a client can list Livy Spark 3 sessions with a Knox JWT through local APISIX, every call is audited, `/cdp/hive` and other unpublished CDP paths 404, and the same token against the raw Knox URL from an untrusted network remains blocked at the CDP perimeter.

Status: local mock path is implemented. Live path is the same Compose file plus `.env`. TLS on the local listener is still HTTP `:9080`.

## Phase 2 — Agent protocol

`mcp-spark` is a Compose upstream. APISIX validates the Knox JWT on `/mcp/spark` and forwards `Authorization`. `mcp-hive` does the same on `/mcp/hive` with read-only Hive tools. `mcp-impala` does the same on `/mcp/impala` with read-only Impala tools. Raw Livy on the agent listener is GET/HEAD only. WebHDFS on the agent listener is GET/HEAD/PUT for operator staging. `/cdp/hive` and `/cdp/impala` stay 404.

Tools: Spark `spark_list_sessions`, `spark_list_batches`, `spark_get_batch`, `spark_get_log`, `spark_submit_batch` (HDFS/object-store `file` only). Hive `hive_list_databases`, `hive_list_tables`, `hive_describe_table`, `hive_select` (named columns, limit ≤ 50). Impala `impala_list_databases`, `impala_list_tables`, `impala_describe_table`, `impala_select` (same caps). Logs are truncated. Interactive Livy `run code` is not exposed.

Status: Spark MCP catalog is implemented, including submit (2b). Hive MCP is read-only (P2-05). Impala MCP is read-only (P2-16). Livy HTTP writes are closed on the agent address (2a). Operator admin UI records usage, joins tool/`sub`/`knox.id` by `X-Request-Id` (P2-04), and enforces per-`sub` quotas. `/mcp/spark`, `/mcp/hive`, and `/mcp/impala` have an APISIX `limit-count` burst cap keyed by Knox `sub` (P2-13). The MCP contract is **POST JSON-RPC**; Streamable HTTP is held. Partner mTLS remains. Live proof (P2-15, 2026-08-17): `spark_submit_batch` of `count_to_10.py` on Public Cloud `go01-obser-de` reached `state=success`; `hive_select` of `{sub}.count_to_10` returned `n` 1..10. Results: [testing.md](testing.md).

## Phase 3 — Third-party ready

Publish `/.well-known/oauth-protected-resource` (RFC 9728) and put `resource_metadata` on `401 WWW-Authenticate`. Require an **agent caller key** (`X-Agent-Key`) on `/mcp/spark`, `/mcp/hive`, and `/mcp/impala` in addition to the Knox JWT. The key names the agent product; it is not a CDP user. Operator Livy GET and WebHDFS stay JWT-only. Check managed Knox token enablement against a host-pinned token-state URL (local mock; live is opt-in). Redact JWT-shaped strings in Spark logs before they return to a model.

Do **not** mint a second user JWT. Do not implement PKCE token exchange until an enterprise IdP can swap into a Knox JWT. mTLS waits on HTTPS at the agent listener.

Status: P3-01 (PRM + `WWW-Authenticate`), P3-02 (mock token-state), P3-03 (MCP `key-auth`), P3-04 (log redaction) are implemented and passed on local mock (2026-08-17, [testing.md](testing.md)). Live stacks set `KNOX_TOKEN_STATE_URL` on the pinned Knox host or skip (signature + `exp` only). AMP stays JWT-only for the agent product (CML project identity) and serves the same PRM. PKCE broker and partner mTLS remain open.

## Suggested repo slices

| Slice | Contents | Depends on |
| --- | --- | --- |
| `deploy/` + `conf/` + `plugins/` | APISIX routes, Knox JWT plugin, Compose | Knox JWKS URL |
| `src/agentgateway/` | Operator CLI | Phase 1 proxy |
| `mcp-spark` | Livy / Spark tools over Knox | Phase 1 Spark proxy | [spark.md](spark.md) |
| `admin/` | Operator UI + sqlite usage/quotas | Phase 2 Spark MCP | [admin.md](admin.md) |
| AMP (`.project-metadata.yaml`) | Optional CML apps (`mcp-spark`, `mcp-hive`, `mcp-impala`, admin); Python JWT; live Knox | Phase 2 MCP | [amp.md](amp.md) |
| `mcp-hive` | Read-only SQL tools over Knox Hive | Phase 1 proxy + JDBC inventory | [hive.md](hive.md) |
| `mcp-impala` | Read-only SQL tools over CDW Impala (`IMPALA_HOST`) or Knox `/impala` | Phase 1 proxy | [impala.md](impala.md) |
| `mcp-catalog` | Atlas / schema discovery tools | `mcp-hive` |
| `policy` | Tool allowlists, row caps | Identity model (admin quotas + request_id audit join) |
| `oauth-adapter` | PRM (done); PKCE broker / token exchange to Knox | IdP decision |

## Open decisions

| Decision | Default | Why it waits |
| --- | --- | --- |
| Private Cloud vs CDP Public Cloud | `gateway knox` parses both `/gateway/cdp-proxy-token` and `/<env>/cdp-proxy-token` | Confirm JWKS path on the target cluster |
| HTTPS on localhost APISIX | HTTP `:9080` for the laptop lab | Add TLS before partner agents leave the VPN; mTLS follows TLS |
| IdP in front of Knox | None; PRM `authorization_servers` empty unless `KNOX_AUTHORIZATION_SERVER` is set | PKCE broker needs an IdP that can exchange into a Knox JWT |
| Streamable HTTP on `/mcp/spark`, `/mcp/hive`, and `/mcp/impala` | POST JSON-RPC only | Hold until a real host fails `initialize`; do not add GET SSE now |
| AMP `launchable: true` | false | Needs a workbench import against live Knox recorded in [testing.md](testing.md) |
| Live Knox token-state URL | Unset (signature + `exp` only) | Confirm TSS path on the target cluster; local mock is `KNOX_TOKEN_STATE_URL` |
