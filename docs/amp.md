# Optional Cloudera AI AMP

This is a **second runtime profile**, not a replacement for Docker Compose. Apache APISIX remains the laptop and production-shaped agent edge. The AMP path is a Cloudera AI Workbench (CML) application that still terminates on Knox.

`METADATA.yaml` stays `launchable: false` until a workbench import against live Knox is recorded in [testing.md](testing.md). Do not advertise one-click catalog launch before that.

Template: [CML Community AMP Template](https://github.com/cloudera/CML_Community_AMP_Template). Spec: [AMP project specification](https://docs.cloudera.com/machine-learning/cloud/applied-ml-prototypes/topics/ml-amp-project-spec.html).

## When to use which

| Profile | Public agent address | JWT check | Upstream |
| --- | --- | --- | --- |
| Compose (default) | APISIX `:9080` | `plugins/knox-jwt.lua` | `mcp-spark` / `mcp-hive` / `mcp-impala` → Knox |
| AMP (optional) | CML Application URLs | Python `knox-jwt` in `agentgateway.knox_jwt` | same adapter code → Knox |

AMP does **not** run Compose, APISIX, Lua plugins, or mock Knox. It is live Knox only (`--mint` does not apply). Do not use CML-native Spark jobs as the Spark path.

```text
MCP host → CML Application (Knox JWT + MCP) → Knox cdp-proxy-token → Livy Spark 3 / Hive, or inventoried CDW Impala
```

## Import

Workbench project creation **clones git first**. AMP jobs do not run until that clone succeeds. `catalog-entry.yaml` `git_url` is `https://github.com/BrooksIan/CDPAgentGateway.git`. That repo is private today, so an unattended HTTPS clone fails with:

```text
Unable to clone … fatal: could not read Username for 'https://github.com': No such device or address
```

Fix clone access **before** launching the AMP:

1. In GitHub, create a personal access token with `repo` scope (classic) or Contents read on `BrooksIan/CDPAgentGateway` (fine-grained). Do not put the token in git or `catalog-entry.yaml`.
2. In the workbench, add Git credentials for `github.com` (user settings, or site administration if AMP catalog clone uses the workspace credential store). Username is the GitHub user; password is the PAT.
3. Create the project from this git URL, or add `catalog-entry.yaml` as a custom AMP source and launch the tile.
4. Set project environment variables. **Required:** `KNOX_PROXY_URL` (Livy-for-Spark-3 on `cdp-proxy-token`). Optional: `KNOX_JWKS_URL` if it is not the URL derived from the proxy URL. Host must match Knox; foreign `jku` values are refused. Optional Impala CDW: `IMPALA_HOST`, `IMPALA_PORT`, `IMPALA_SCHEME`, `IMPALA_HTTP_PATH` (`cliservice`). JDBC `auth=browser` is not used; the app forwards the Knox JWT.
5. Runtime: Workbench, Python 3.11, Standard. No GPU.
6. Let AMP tasks run: install extras (session), fetch pinned JWKS, smoke-check Knox, start applications.

A public clone does not need a PAT. Do not encode a token in `git_url`.

Do not put `KNOX_TOKEN` in project env or git. Paste the Knox JWT into the MCP host secret store.

If the project clones but **applications stay Starting or Failed**, open Application logs. Typical causes:

- Install wrote packages into the job engine instead of `/home/cdsw/.local` (`pip install --user` is required).
- The process exited before listening on `127.0.0.1:$CDSW_APP_PORT` (CML probes loopback, not `0.0.0.0`).
- A Knox JWKS job failed and the runbook never reached `start_application`. Fetch/smoke now warn and continue; MCP POSTs still fail closed without a PEM.
- Static subdomains `mcp-spark`, `mcp-hive`, `mcp-impala`, or `gateway-admin` already exist from a previous AMP attempt.

Push this repo to GitHub, then **Update** the AMP (or delete the project and relaunch) so the workbench picks up the app scripts.

## Applications

| Subdomain | CML login | Role |
| --- | --- | --- |
| `mcp-spark` | Bypassed (MCP hosts cannot send CML cookies) | POST JSON-RPC Spark. Knox JWT required. GET `/` and `/health` are public. |
| `mcp-hive` | Bypassed | POST JSON-RPC Hive (read-only). Knox JWT required. `/cdp/hive` is not this app. |
| `mcp-impala` | Bypassed | POST JSON-RPC Impala (read-only). Knox JWT required. `/cdp/impala` is not this app. CDW `HTTP code 401` after a valid JWT is warehouse trust, not APISIX. |
| `gateway-admin` | Required | Operator usage/quotas. Shares `data/gateway.sqlite`. Not an agent route. |

Quotas use sqlite on the project filesystem (`ADMIN_BACKEND=sqlite`). Compose still uses HTTP to the admin container and fails open if that container is down.

Burst cap `MCP_RATE_COUNT` / `MCP_RATE_WINDOW` is enforced in the AMP JWT middleware (APISIX `limit-count` is Compose-only).

## MCP host config

The application URL is the MCP endpoint (POST JSON-RPC, no Streamable HTTP):

```json
{
  "mcpServers": {
    "cdp-spark": {
      "url": "https://mcp-spark.<workspace>/mcp/spark",
      "headers": {
        "Authorization": "Bearer <knox-jwt>"
      }
    },
    "cdp-hive": {
      "url": "https://mcp-hive.<workspace>/mcp/hive",
      "headers": {
        "Authorization": "Bearer <knox-jwt>"
      }
    },
    "cdp-impala": {
      "url": "https://mcp-impala.<workspace>/mcp/impala",
      "headers": {
        "Authorization": "Bearer <knox-jwt>"
      }
    }
  }
}
```

`/` and `/mcp` also accept POST JSON-RPC after a valid Knox JWT. Put the JWT in the host secret store, never in git.

## Identity

| Principal | AMP |
| --- | --- |
| End user | Knox JWT (`sub`, `knox.id`) — same as Compose |
| Agent platform | CML project + `mcp-spark` / `mcp-hive` / `mcp-impala` subdomain (JWT-only; Compose MCP uses `X-Agent-Key`) |
| Authorization | Ranger via Knox; no impersonation |

AMP is JWT-only for the agent product. Compose MCP caller keys and Phase 3 mTLS do not map onto CML application URLs. AMP still publishes `/.well-known/oauth-protected-resource` and `resource_metadata` on `401`.

## Operator files

| Path | Role |
| --- | --- |
| `.project-metadata.yaml` | AMP runbook (CML ignores Compose) |
| `catalog-entry.yaml` | Optional custom catalog snippet |
| `assets/AMP_thumbnail.jpg` | Catalog tile cover (`image_path`) |
| `0_session-install-dependencies/` | `pip install --user -e ".[amp]"` (hive extra is best-effort) |
| `1_job-fetch-jwks/` | Pin JWKS → `conf/generated/knox-public.pem` |
| `2_job-smoke-knox/` | PEM + JWKS reachability; optional Livy GET |
| `3_app-mcp-spark/` | Spark MCP application |
| `4_app-operator-admin/` | Admin application |
| `5_app-mcp-hive/` | Hive MCP application |
| `6_app-mcp-impala/` | Impala MCP application |

## Non-goals

- Docker-in-CML, APISIX, or mock-cdp
- Raw Livy writes, `/cdp/hive`, `/cdp/impala`, Ozone, NiFi as agent routes
- `/cdp/webhdfs` (Compose APISIX only; AMP has no APISIX)
- APISIX `jwt-auth` or a CML model access key as the CDP user
- Streamable HTTP unless a real host fails `initialize`

After a successful workbench proof, set `launchable: true`, add `Launchable (AMP)` to `catalog_classification`, and add Cloudera AI to `product_mapping`. Until then keep `launchable: false`.
