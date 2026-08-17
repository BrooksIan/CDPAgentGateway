# Test cases

Phase 0 defines cases. Phase 1 executes them against **local APISIX** (mock Knox or live Knox). Mark live results in the table at the bottom; do not commit tokens.

Assumptions:

- `GATEWAY_URL` is `http://127.0.0.1:9080` unless `APISIX_PORT` is changed
- Live Knox URL is set with `gateway knox <https-url>`
- Hive JDBC (optional) is stored with `gateway jdbc add <jdbc:hive2://…>` and is not an agent route
- `KNOX_TOKEN` is a Knox JWT in `.env` (`gateway token set`), never in git
- Phase 1 probe is `GET /cdp/livy_for_spark3/sessions`

## How to run

```bash
gateway test --unit          # inventory, CLI, blueprint layout (no Docker)
gateway test                 # start Compose, run pytest -m "not live"
gateway test --live          # requires GATEWAY_MODE=live and KNOX_TOKEN
```

`make test` / `make test-unit` / `make live` call the same CLI.

## Phase 0 — fixtures

| ID | Case | Expected | Automated |
| --- | --- | --- | --- |
| P0-01 | Inventory has Knox fields and a read-only first tool | `inventory/cdp.yaml` schema; worksheet in [phase-0-inventory.md](phase-0-inventory.md) | `tests/test_phase0_inventory.py` |
| P0-02 | Laptop can resolve and connect to Knox | TLS handshake to live Knox | `gateway doctor`; live only |
| P0-03 | JWKS fetches from the pinned host | `GET` JWKS returns keys; host matches `UPSTREAM_HOST` | `gateway fetch-jwks`; `trusted_jku` in `tests/test_knox_url.py` |
| P0-04 | Operator can mint or paste a JWT | `iss=KNOXSSO`, `alg=RS256`, `sub`, `exp` | `gateway token mint` (mock); `gateway token show` (live) |
| P0-05 | Probe user is allowed by Ranger for Livy Spark 3 | Direct Knox or gateway `GET .../sessions` succeeds | `tests/test_live_cdp.py` |
| P0-06 | `.env` is gitignored | `git check-ignore .env` is true | `.gitignore` |
| P0-07 | Hive JDBC `cdp-proxy-api` is stored without replacing Livy | `HIVE_KNOX_PREFIX` set; `KNOX_PROXY_PREFIX` unchanged | `tests/test_knox_url.py` |
| P0-08 | Hive JWT probe uses token topology `/hive` | `gateway hive` → `SHOW DATABASES`; not `/cdp/hive` | `tests/test_hive.py` |

## Phase 1 — gateway perimeter

Against mock CDP unless noted. Spark URI is `/cdp/livy_for_spark3/sessions`.

| ID | Case | Expected | Automated |
| --- | --- | --- | --- |
| P1-01 | Missing `Authorization` | `401`, `reason=missing_token`, `WWW-Authenticate: Bearer` | `tests/test_gateway_auth.py` |
| P1-02 | Malformed bearer | `401`; no upstream Livy body | same |
| P1-03 | Valid Knox-shaped JWT + sessions path | `2xx`; `Authorization` forwarded; `knox_user` matches `sub` | `tests/test_gateway_auth.py`, `tests/test_gateway_proxy.py` |
| P1-04 | Expired JWT | `401` `expired` | `tests/test_gateway_auth.py` |
| P1-05 | Wrong `iss`, `alg=none`, or HS256 confusion | `401` | `tests/test_gateway_auth.py` |
| P1-06 | Valid JWT, path outside Spark allowlist (`/cdp/hive`) | `404` | `tests/test_gateway_proxy.py` |
| P1-07 | Request ID present | `X-Request-Id` on `/health` and Spark routes | `tests/test_gateway_auth.py`, `tests/test_gateway_proxy.py` |
| P1-08 | Auth failure reason is visible | `X-Agent-Gateway-Reason` / JSON `reason`; **no raw token** | `tests/test_gateway_auth.py` |
| P1-09 | Auth success binds the user | `X-Knox-User` and `X-Knox-Token-Id` forwarded upstream | mock CDP echoes `knox_user` / `token_id` |
| P1-10 | JWKS host pinning | Token `jku` on a foreign host is refused by the CLI | `tests/test_knox_url.py` (not the Lua plugin) |

## Phase 1 — CDP still behind Knox

| ID | Case | Expected | Automated |
| --- | --- | --- | --- |
| P1-11 | Ranger deny user through gateway | CDP/Knox denies; gateway does not override | live only; not a dedicated deny fixture yet |
| P1-12 | Gateway does not mint credentials | Upstream sees the caller bearer (`hide_credentials: false`) | mock asserts `authorization_present` |
| P1-13 | Direct Knox from untrusted network | Blocked by CDP perimeter | Manual; record how the cluster enforces it |

## Phase 2 — Spark MCP

| ID | Case | Expected | Automated |
| --- | --- | --- | --- |
| P2-01 | MCP `tools/list` through APISIX | `200`; Spark tools listed including `spark_submit_batch` | `tests/test_mcp_spark.py` |
| P2-02 | MCP without bearer | `401` | `tests/test_mcp_spark.py` |
| P2-03 | `spark_list_batches` forwards `sub` | JSON result includes `knox_user`; no raw token | `tests/test_mcp_spark.py` |
| P2-04 | Audit record joins tool, `sub`, `knox.id` | `GET /api/audit?request_id=` returns those fields; no bearer | `tests/test_admin_store.py`, `tests/test_admin_gateway.py` |
| P2-06 | Raw Livy writes on the agent listener | Authenticated `POST .../sessions/0/statements`, `POST .../batches`, `PUT`, `DELETE` → `404` or `405` | `tests/test_gateway_proxy.py` |
| P2-07 | `spark_submit_batch` accepts cluster file URI | HDFS (or other allowlisted scheme) submit `isError=false`; `submitted=true` | `tests/test_mcp_spark.py` |
| P2-08 | `spark_submit_batch` rejects remote HTTP file | `isError=true`; error names HDFS/object-store | `tests/test_mcp_spark.py` |
| P2-09 | `spark_submit_batch` rejects `proxyUser` | `isError=true`; does not impersonate | `tests/test_mcp_spark.py`, `tests/test_mcpspark_livy.py` |
| P2-10 | `spark_submit_batch` rejects inline `code` | `isError=true`; file URI required | `tests/test_mcp_spark.py`, `tests/test_mcpspark_livy.py` |
| P2-11 | Operator admin UI on `:9090` | `GET /health` 200; `GET /admin` on APISIX is 404 | `tests/test_admin_gateway.py` |
| P2-12 | Per-user submit quota | `daily_submits=0` → admin admit `429`; MCP `isError` when mock PEM is in use | `tests/test_admin_gateway.py`, `tests/test_admin_store.py` |
| P2-13 | MCP burst rate limit | Authenticated `/mcp/spark` returns `429` after `MCP_RATE_COUNT` per Knox `sub`; Livy GET is not capped | `tests/test_gateway_ratelimit.py`, `tests/test_apisix_render.py` |

## Later phases (do not implement yet)

| ID | Case | Phase |
| --- | --- | --- |
| P2-05 | Hive MCP through APISIX | 2 (`mcp-hive` not started) |
| — | Streamable HTTP / GET SSE / MCP session | Held; POST JSON-RPC is the `/mcp/spark` contract |
| P3-01 | `401` includes RFC 9728 resource metadata | 3 |
| P3-02 | Revoked-but-unexpired Knox token is rejected | 3 |
| P3-03 | Partner without mTLS/caller key is rejected | 3 |

## Results log

Copy a row per live run. Keep this table free of secrets.

| Date | IDs run | Environment | Pass / fail | Notes |
| --- | --- | --- | --- | --- |
| | | | | |
