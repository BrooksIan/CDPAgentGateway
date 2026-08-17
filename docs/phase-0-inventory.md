# Phase 0 inventory

Fill this against the **external CDP** that local Docker APISIX will call. Machine-readable copy: [`inventory/cdp.yaml`](../inventory/cdp.yaml) (pytest asserts that schema). Runtime values go in `.env` via `gateway knox`, `gateway jdbc add`, and `gateway token set`. Do not paste tokens or private keys into this file.

## Environment

| Item | Value |
| --- | --- |
| Deployment | CDP Public Cloud |
| Cluster / environment name | `go01-obser-de` |
| Network path from laptop | Direct to Knox `*.cloudera.site` (laptop APISIX Compose) |
| Operator contact | |

## Knox

Paste the Livy-for-Spark3 proxy URL into the CLI instead of hand-editing prefixes:

```bash
gateway knox https://knox.example.com/<env>/cdp-proxy-token/livy_for_spark3/
gateway fetch-jwks --insecure
```

| Item | Value |
| --- | --- |
| Knox homepage / gateway origin | `https://go01-obser-de-gateway.go01-dem.ylcu-atmi.cloudera.site` |
| Topology used for token APIs | `cdp-proxy-token` |
| Livy for Spark 3 proxy URL | `.../go01-obser-de/cdp-proxy-token/livy_for_spark3/` |
| Token API URL (v1 or v2) | v2 (`.../homepage/knoxtoken/api/v2/`) |
| JWKS URL (pin this host) | `.../go01-obser-de/homepage/knoxtoken/api/v2/jwks.json` |
| Token issuer (`iss`) | `KNOXSSO` |
| Signing algorithm | RS256 |
| Default token TTL | managed token (long-lived); do not log the bearer |
| Impersonation / Trusted Proxy enabled? | yes (Livy runs as Knox `sub`) |
| Can tokens be revoked/disabled while unexpired? | yes (managed.token); Phase 1 still trusts `exp` only |

How the operator will mint a test JWT (Token Generation UI / Token API / other):

Then: `gateway token set` (paste JWT; claims only are printed).

## First CDP services (read-only)

Start with Spark. Mark the others as later. Gateway path is `/cdp/<knox-service>/...`.

| Service | Knox path / topology | Read-only probe | In Phase 1? |
| --- | --- | --- | --- |
| Spark / Livy for Spark 3 | `.../cdp-proxy-token/livy_for_spark3/` | `GET /sessions` → `gateway spark` | **yes** — [spark.md](spark.md) |
| HDFS / WebHDFS | `.../cdp-proxy-token/webhdfs/v1/` | `GET ?op=LISTSTATUS` → `gateway webhdfs ls` | **yes** (operator staging) — [spark.md](spark.md) |
| Hive / HS2 HTTP | `gateway jdbc add '<jdbc:hive2://…;httpPath=…/cdp-proxy-api/hive>'` then `/mcp/hive` | `hive_list_databases` | **yes (Phase 2 MCP)**; `/cdp/hive` 404 — [hive.md](hive.md) |
| Impala | | | no |
| Ozone / S3 | | list bucket or prefix | no |
| Atlas | | search or type def | no |
| NiFi | | read flow / about | no |

## Ranger

| Item | Value |
| --- | --- |
| Test user (`sub`) | `ibrooks` |
| Groups (`knox.groups` if present) | not present on the live JWT |
| Policies that should allow Livy Spark 3 session list | Ranger allows `ibrooks` Livy Spark 3 + Hive on `ibrooks.*` (observed via live submit/select) |
| Policies that should deny a negative test | not recorded |

## Agent preview (Phase 1 is CLI/curl, not MCP)

| Item | Value |
| --- | --- |
| Intended first agent host | Cursor (MCP `/mcp/spark` and `/mcp/hive`) |
| Tool names to allow later | Spark list/get/log/submit; Hive list/describe/select |
| Data that must never return to a model | Raw Knox bearer; unbounded Hive/Spark result sets |

## Threat model (Phase 0 notes)

Record anything specific to this cluster. Defaults in [testing.md](testing.md):

- Prompt injection that becomes Spark SQL or shell in a Livy session
- Wide DataFrame / `collect()` dumped into an LLM context
- Shared or leaked Knox bearer used by a second agent
- Direct Knox URL access bypassing this gateway
- Calling Hive or other Knox services through a catch-all proxy (must 404)

## Blockers

None for Phase 1/2 on `go01-obser-de` (2026-08-17). Livy is on `cdp-proxy-token`; Hive MCP uses the token topology `/hive`. Do not pass `--mint` against Knox JWKS. Phase 3: Compose MCP needs `X-Agent-Key`; live Knox token-state URL is unset (signature + `exp` only). PKCE broker and mTLS still open.
