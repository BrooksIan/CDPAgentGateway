# Architecture

Third-party agents must not discover Livy, Hive, Impala, Ozone, NiFi, or other CDP endpoints. They talk only to this gateway. Phase 1 allowlists **Livy for Spark 3** (`GET`/`HEAD` `/cdp/livy_for_spark3*`) and **WebHDFS** (`GET`/`HEAD`/`PUT` `/cdp/webhdfs*`).

![CDP Agent Gateway traffic path](../assets/architecture.svg)

Phase 1 traffic is `agents → APISIX → Knox → Livy (Spark 3)` plus operator HDFS staging `→ APISIX → Knox → WebHDFS`. Phase 2 adds `agents → APISIX → mcp-spark → Knox → Livy` and `agents → APISIX → mcp-hive → Knox → Hive`. `/cdp/hive` stays **404**. Optional AMP is `agents → CML Application → mcp-spark or mcp-hive → Knox`. AMP does not publish WebHDFS; that route is Compose APISIX.

## What each hop owns

| Hop | Owns | Must not own |
| --- | --- | --- |
| Operator CLI (`gateway`) | `.env`, mock keys, JWKS fetch, Compose, probes (`gateway spark`, `gateway webhdfs`) | Cluster credentials, Ranger decisions |
| Operator admin (`:9090`) | Usage by Knox `sub`, tool quotas, `request_id` audit join | Agent traffic, Ranger decisions, cluster credentials |
| Agent Gateway (APISIX) | TLS (later), Knox JWT validation, allowlisted routes, request IDs, MCP burst cap | Cluster credentials, Ranger decisions, MCP tool implementations |
| AMP mcp-spark / mcp-hive apps (optional) | Same Knox JWT rules in Python; CML Application URL | Compose, APISIX, mock Knox, impersonation, WebHDFS |
| MCP adapters (`mcp-spark`, `mcp-hive`) | Spark list/get/log/submit; Hive list/describe/select; forward the caller's Knox bearer | Impersonation, inline Spark code, free-form Hive SQL, long-lived Knox secrets |
| Knox | Token issuance, `JWTProvider` on `cdp-proxy-token`, Trusted Proxy / doAs, WebHDFS | Public internet exposure |
| Ranger | Data authorization for the Knox subject on Spark, Hive, and HDFS | Agent-product identity |

## Phase 1 routes

APISIX standalone config is rendered from `conf/apisix.yaml.tpl` into `conf/generated/apisix.yaml`.

| Gateway URI | Methods | Auth | Upstream rewrite |
| --- | --- | --- | --- |
| `/health` | GET | None | Mock JSON on APISIX (does not call Knox) |
| `/.well-known/oauth-protected-resource` | GET | None | RFC 9728 protected-resource metadata (does not call Knox) |
| `/cdp/livy_for_spark3*` | GET, HEAD | `knox-jwt` | `{KNOX_PROXY_PREFIX}/livy_for_spark3...` |
| `/cdp/webhdfs*` | GET, HEAD, PUT | `knox-jwt` | `{KNOX_PROXY_PREFIX}/webhdfs...` (v1 and data path) |
| `/mcp/spark*` | GET, HEAD, POST, DELETE | `knox-jwt` + `key-auth` + `limit-count` | `mcp-spark:8080/mcp` |
| `/mcp/hive*` | GET, HEAD, POST, DELETE | `knox-jwt` + `key-auth` + `limit-count` | `mcp-hive:8080/mcp` |

Example: `GET http://127.0.0.1:9080/cdp/livy_for_spark3/sessions` becomes `GET {knox}/gateway/cdp-proxy-token/livy_for_spark3/sessions` (prefix varies on Public Cloud). `POST`/`PUT`/`DELETE` on that Livy prefix return **404** (or 405). Submit goes through `/mcp/spark` (`spark_submit_batch`). Interactive `POST .../sessions/{id}/statements` is not a route.

WebHDFS is the operator staging hop for Spark `file` URIs. `GET`/`HEAD` list and stat; `PUT` is MKDIRS and CREATE (including Knox's `/webhdfs/data/v1` follow-up). `DELETE` is **not** published. CREATE with `noredirect=true` returns a Knox `Location`; `gateway webhdfs put` rewrites that Location onto this gateway and refuses a foreign host. How-to: [spark.md](spark.md).

`GET /cdp/hive` and other unpublished CDP services return **404**. Hive agents use `POST /mcp/hive`. Inventory a Hive JDBC URL with `gateway jdbc add`. Operators can run `gateway hive` (`SHOW DATABASES`) against Knox `{KNOX_PROXY_PREFIX}/hive`. How-to: [spark.md](spark.md), [hive.md](hive.md).

## Non-goals

- Do not treat APISIX as a dedicated MCP gateway. Streamable HTTP is held; tool servers stay as real services. APISIX may proxy it later if a host requires it.
- Do not build on the experimental APISIX `mcp-bridge` (stdio → SSE) prototype.
- Do not replace Knox with AISIX. AISIX has a stronger MCP/A2A catalog, but no Knox JWTProvider, Trusted Proxy, or Ranger story.
- Do not expose `cdp-proxy-token` to untrusted networks. The gateway is the only public address agents should see.
- Do not use APISIX `jwt-auth` or `openid-connect` as the Knox authenticator. This repo ships `plugins/knox-jwt.lua`.

## Traffic classes

Use Backend-for-Frontend routes per agent class rather than one catch-all proxy:

- Phase 1: Livy for Spark 3 (`GET /cdp/livy_for_spark3/sessions` via `gateway spark`) — [spark.md](spark.md)
- Phase 1: WebHDFS (`/cdp/webhdfs*`) for operator file staging (`gateway webhdfs`) — [spark.md](spark.md)
- Phase 2: MCP Spark at `/mcp/spark` and read-only Hive at `/mcp/hive`. `/cdp/hive` stays 404.
- Phase 3: RFC 9728 PRM; MCP caller key (`X-Agent-Key`); optional Knox token-state; Spark log redaction. PKCE broker and mTLS wait.
- Hive: MCP `/mcp/hive` (list/describe/select); JDBC inventory; `/cdp/hive` 404 — [hive.md](hive.md)
- Partner write (explicitly allowlisted tools only)
- Internal ops (operator admin UI on `127.0.0.1:9090`, usage, quotas, audit join) — [admin.md](admin.md)

Spark jobs are long-running. Upstream send/read timeouts in APISIX are 120s; traces should start at the agent request and continue through Knox.

## Local, live, and AMP shape

A laptop runs APISIX in Docker.

- **Local:** upstream is `mock-cdp`. `gateway init && gateway up && gateway test` is the lab path. `--mint` signs the lab RSA key that APISIX loads.
- **Live:** `gateway knox <https-knox-url-with-livy_for_spark3>` writes host, `cdp-proxy-token` prefix, and JWKS URL into `.env`. `gateway token set` stores the Knox JWT. Agents and curl still hit `localhost:9080`; APISIX validates the JWT and proxies Livy, WebHDFS, `/mcp/spark`, and `/mcp/hive`. `--mint` is refused.
- **Optional AMP:** Cloudera AI Workbench applications (`mcp-spark`, `mcp-hive`, `gateway-admin`). Python Knox JWT in front of the same adapters. Live Knox only; no Compose, no mock CDP, no `/cdp/webhdfs`. `launchable` stays false until a workbench proof. How-to: [amp.md](amp.md).

Direct Knox access from untrusted networks stays blocked at the CDP perimeter.
