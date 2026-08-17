# Optional Cloudera AI AMP

This is a **second runtime profile**, not a replacement for Docker Compose. Apache APISIX remains the laptop and production-shaped agent edge. The AMP path is a Cloudera AI Workbench (CML) application that still terminates on Knox.

`METADATA.yaml` stays `launchable: false` until a workbench import against live Knox is recorded in [testing.md](testing.md). Do not advertise one-click catalog launch before that.

Template: [CML Community AMP Template](https://github.com/cloudera/CML_Community_AMP_Template). Spec: [AMP project specification](https://docs.cloudera.com/machine-learning/cloud/applied-ml-prototypes/topics/ml-amp-project-spec.html).

## When to use which

| Profile | Public agent address | JWT check | Upstream |
| --- | --- | --- | --- |
| Compose (default) | APISIX `:9080` | `plugins/knox-jwt.lua` | `mcp-spark` container → Knox |
| AMP (optional) | CML Application `https://mcp-spark.<workspace>/` | Python `knox-jwt` in `agentgateway.knox_jwt` | same `mcp-spark` code → Knox |

AMP does **not** run Compose, APISIX, Lua plugins, or mock Knox. It is live Knox only. Do not use CML-native Spark jobs as the Spark path.

```text
MCP host → CML Application (Knox JWT + MCP) → Knox cdp-proxy-token → Livy Spark 3
```

## Import

1. In the workbench, create a project from this git repo (or add `catalog-entry.yaml` as a custom AMP source).
2. Set project environment variables. **Required:** `KNOX_PROXY_URL` (Livy-for-Spark-3 on `cdp-proxy-token`). Optional: `KNOX_JWKS_URL` if it is not the URL derived from the proxy URL. Host must match Knox; foreign `jku` values are refused.
3. Runtime: Workbench, Python 3.11, Standard. No GPU.
4. Let AMP tasks run: install extras, fetch pinned JWKS, smoke-check Knox, start applications.

Do not put `KNOX_TOKEN` in project env or git. Paste the Knox JWT into the MCP host secret store.

## Applications

| Subdomain | CML login | Role |
| --- | --- | --- |
| `mcp-spark` | Bypassed (MCP hosts cannot send CML cookies) | POST JSON-RPC. Knox JWT required. GET `/` and `/health` are public. |
| `gateway-admin` | Required | Operator usage/quotas. Shares `data/gateway.sqlite` with mcp-spark. Not an agent route. |

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
    }
  }
}
```

`/` and `/mcp` also accept POST JSON-RPC after a valid Knox JWT. Put the JWT in the host secret store, never in git.

## Identity

| Principal | AMP |
| --- | --- |
| End user | Knox JWT (`sub`, `knox.id`) — same as Compose |
| Agent platform | CML project + `mcp-spark` subdomain until Phase 3 caller keys |
| Authorization | Ranger via Knox; no impersonation |

AMP is JWT-only. Phase 3 mTLS does not map onto CML application URLs.

## Operator files

| Path | Role |
| --- | --- |
| `.project-metadata.yaml` | AMP runbook (CML ignores Compose) |
| `catalog-entry.yaml` | Optional custom catalog snippet |
| `assets/AMP_thumbnail.jpg` | Catalog tile cover (`image_path`) |
| `0_session-install-dependencies/` | `pip install -e ".[amp]"` |
| `1_job-fetch-jwks/` | Pin JWKS → `conf/generated/knox-public.pem` |
| `2_job-smoke-knox/` | PEM + JWKS reachability; optional Livy GET |
| `3_app-mcp-spark/` | MCP application |
| `4_app-operator-admin/` | Admin application |

## Non-goals

- Docker-in-CML, APISIX, or mock-cdp
- Raw Livy writes, Hive, Impala, Ozone, NiFi as agent routes
- APISIX `jwt-auth` or a CML model access key as the CDP user
- Streamable HTTP unless a real host fails `initialize`

After a successful workbench proof, set `launchable: true`, add `Launchable (AMP)` to `catalog_classification`, and add Cloudera AI to `product_mapping`. Until then keep `launchable: false`.
