# Architecture

Third-party agents must not discover Livy, Hive, Impala, Ozone, NiFi, or other CDP endpoints. They talk only to this gateway. Phase 1 allowlists **Livy for Spark 3** only (`/cdp/livy_for_spark3*`).

![CDP Agent Gateway traffic path](../assets/architecture.svg)

Phase 1 traffic is `agents → APISIX → Knox → Livy (Spark 3)`. Phase 2 adds `agents → APISIX → mcp-spark → Knox → Livy`.

## What each hop owns

| Hop | Owns | Must not own |
| --- | --- | --- |
| Operator CLI (`gateway`) | `.env`, mock keys, JWKS fetch, Compose, probes (`gateway spark`) | Cluster credentials, Ranger decisions |
| Operator admin (`:9090`) | Usage by Knox `sub`, tool quotas, `request_id` audit join | Agent traffic, Ranger decisions, cluster credentials |
| Agent Gateway (APISIX) | TLS (later), Knox JWT validation, allowlisted routes, request IDs, MCP burst cap | Cluster credentials, Ranger decisions, MCP tool implementations |
| MCP adapters (`mcp-spark`) | Livy list/get/log/submit; forward the caller's Knox bearer | Impersonation, inline Spark code, long-lived Knox secrets |
| Knox | Token issuance, `JWTProvider` on `cdp-proxy-token`, Trusted Proxy / doAs | Public internet exposure |
| Ranger | Data authorization for the Knox subject on Spark | Agent-product identity |

## Phase 1 routes

APISIX standalone config is rendered from `conf/apisix.yaml.tpl` into `conf/generated/apisix.yaml`.

| Gateway URI | Methods | Auth | Upstream rewrite |
| --- | --- | --- | --- |
| `/health` | GET | None | Mock JSON on APISIX (does not call Knox) |
| `/cdp/livy_for_spark3*` | GET, HEAD | `knox-jwt` | `{KNOX_PROXY_PREFIX}/livy_for_spark3...` |
| `/mcp/spark*` | GET, HEAD, POST, DELETE | `knox-jwt` + `limit-count` | `mcp-spark:8080/mcp` |

Example: `GET http://127.0.0.1:9080/cdp/livy_for_spark3/sessions` becomes `GET {knox}/gateway/cdp-proxy-token/livy_for_spark3/sessions` (prefix varies on Public Cloud). `POST`/`PUT`/`DELETE` on that prefix return **404** (or 405). Submit and other writes go through `/mcp/spark` (`spark_submit_batch`), not raw Livy. Interactive `POST .../sessions/{id}/statements` is not a route.

`GET /cdp/hive` and other CDP services return **404**. Inventory a Hive JDBC URL with `gateway jdbc add`; that writes `HIVE_*` in `.env` and does not create an agent route. Operators can run `gateway hive` (`SHOW DATABASES`) against Knox `{KNOX_PROXY_PREFIX}/hive` with the stored JWT. How-to: [spark.md](spark.md), [hive.md](hive.md).

## Non-goals

- Do not treat APISIX as a dedicated MCP gateway. Streamable HTTP is held; tool servers stay as real services. APISIX may proxy it later if a host requires it.
- Do not build on the experimental APISIX `mcp-bridge` (stdio → SSE) prototype.
- Do not replace Knox with AISIX. AISIX has a stronger MCP/A2A catalog, but no Knox JWTProvider, Trusted Proxy, or Ranger story.
- Do not expose `cdp-proxy-token` to untrusted networks. The gateway is the only public address agents should see.
- Do not use APISIX `jwt-auth` or `openid-connect` as the Knox authenticator. This repo ships `plugins/knox-jwt.lua`.

## Traffic classes

Use Backend-for-Frontend routes per agent class rather than one catch-all proxy:

- Phase 1: Livy for Spark 3 (`GET /cdp/livy_for_spark3/sessions` via `gateway spark`) — [spark.md](spark.md)
- Phase 2: MCP Spark tools at `/mcp/spark` (list/get/log plus **write** `spark_submit_batch`)
- Hive: JDBC inventory only until `mcp-hive` — [hive.md](hive.md)
- Partner write (explicitly allowlisted tools only)
- Internal ops (operator admin UI on `127.0.0.1:9090`, usage, quotas, audit join) — [admin.md](admin.md)

Spark jobs are long-running. Upstream send/read timeouts in APISIX are 120s; traces should start at the agent request and continue through Knox.

## Local and live shape

A laptop runs APISIX in Docker.

- **Local:** upstream is `mock-cdp`. `gateway init && gateway up && gateway test` is the lab path.
- **Live:** `gateway knox <https-knox-url-with-livy_for_spark3>` writes host, `cdp-proxy-token` prefix, and JWKS URL into `.env`. `gateway token set` stores the Knox JWT. Agents and curl still hit `localhost:9080`; APISIX validates the JWT and proxies Livy only.

Direct Knox access from untrusted networks stays blocked at the CDP perimeter.
