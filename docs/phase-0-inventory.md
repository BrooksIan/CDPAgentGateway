# Phase 0 inventory

Fill this against the **external CDP** that local Docker APISIX will call. Machine-readable copy: [`inventory/cdp.yaml`](../inventory/cdp.yaml) (pytest asserts that schema). Runtime values go in `.env` via `gateway knox`, `gateway jdbc add`, and `gateway token set`. Do not paste tokens or private keys into this file.

## Environment

| Item | Value |
| --- | --- |
| Deployment | Private Cloud Base / CDP Public Cloud / other: |
| Cluster / environment name | |
| Network path from laptop | VPN / allowlisted IP / jump host: |
| Operator contact | |

## Knox

Paste the Livy-for-Spark3 proxy URL into the CLI instead of hand-editing prefixes:

```bash
gateway knox https://knox.example.com/<env>/cdp-proxy-token/livy_for_spark3/
gateway fetch-jwks --insecure
```

| Item | Value |
| --- | --- |
| Knox homepage / gateway origin | |
| Topology used for token APIs | usually `cdp-proxy-token` |
| Livy for Spark 3 proxy URL | `.../cdp-proxy-token/livy_for_spark3/` |
| Token API URL (v1 or v2) | |
| JWKS URL (pin this host) | `.../homepage/knoxtoken/api/v1/jwks.json` |
| Token issuer (`iss`) | expect `KNOXSSO` |
| Signing algorithm | expect RS256 |
| Default token TTL | |
| Impersonation / Trusted Proxy enabled? | yes / no |
| Can tokens be revoked/disabled while unexpired? | yes / no |

How the operator will mint a test JWT (Token Generation UI / Token API / other):

Then: `gateway token set` (paste JWT; claims only are printed).

## First CDP services (read-only)

Start with Spark. Mark the others as later. Gateway path is `/cdp/<knox-service>/...`.

| Service | Knox path / topology | Read-only probe | In Phase 1? |
| --- | --- | --- | --- |
| Spark / Livy for Spark 3 | `.../cdp-proxy-token/livy_for_spark3/` | `GET /sessions` → `gateway spark` | **yes** — [spark.md](spark.md) |
| HDFS / WebHDFS | `.../cdp-proxy-token/webhdfs/v1/` | `GET ?op=LISTSTATUS` → `gateway webhdfs ls` | **yes** (operator staging) — [spark.md](spark.md) |
| Hive / HS2 HTTP | `gateway jdbc add '<jdbc:hive2://…;httpPath=…/cdp-proxy-api/hive>'` | inventoried only | no agent route — [hive.md](hive.md) |
| Impala | | | no |
| Ozone / S3 | | list bucket or prefix | no |
| Atlas | | search or type def | no |
| NiFi | | read flow / about | no |

## Ranger

| Item | Value |
| --- | --- |
| Test user (`sub`) | |
| Groups (`knox.groups` if present) | |
| Policies that should allow Livy Spark 3 session list | |
| Policies that should deny a negative test | |

## Agent preview (Phase 1 is CLI/curl, not MCP)

| Item | Value |
| --- | --- |
| Intended first agent host | Cursor / Claude / other |
| Tool names to allow later | Spark session list, then job submit |
| Data that must never return to a model | |

## Threat model (Phase 0 notes)

Record anything specific to this cluster. Defaults in [testing.md](testing.md):

- Prompt injection that becomes Spark SQL or shell in a Livy session
- Wide DataFrame / `collect()` dumped into an LLM context
- Shared or leaked Knox bearer used by a second agent
- Direct Knox URL access bypassing this gateway
- Calling Hive or other Knox services through a catch-all proxy (must 404)

## Blockers

List anything that prevents Phase 1 (no JWKS, laptop cannot reach Knox, token TTL too long with no revoke, Livy not on `cdp-proxy-token`, etc.).
