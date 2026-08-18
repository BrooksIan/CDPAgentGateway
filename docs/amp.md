# Optional Cloudera AI AMP

This is a **second runtime profile**, not a replacement for Docker Compose. Apache APISIX remains the laptop and production-shaped agent edge. The AMP path is a Cloudera AI Workbench (CML) application that still terminates on Knox.

`METADATA.yaml` stays `launchable: false` until a workbench import against live Knox is recorded in [testing.md](testing.md). Do not advertise one-click catalog launch before that.

Template: [CML Community AMP Template](https://github.com/cloudera/CML_Community_AMP_Template). Spec: [AMP project specification](https://docs.cloudera.com/machine-learning/cloud/applied-ml-prototypes/topics/ml-amp-project-spec.html).

## When to use which

| Profile | Public agent address | JWT check | Upstream |
| --- | --- | --- | --- |
| Compose (default) | APISIX `:9080` | `plugins/knox-jwt.lua` | `mcp-spark` / `mcp-hive` / `mcp-impala` → Knox |
| AMP (optional) | **`agent-gateway` CML app (APISIX)** | same `knox-jwt.lua` in Docker | sibling MCP apps → Knox |

AMP also publishes direct MCP application URLs (`mcp-spark`, `mcp-hive`, `mcp-impala`) with Python Knox JWT for debugging. **Agents should use `https://agent-gateway.<workspace>/mcp/spark`** (and hive/impala) so they get the same APISIX routes as Compose: `/cdp/livy_for_spark3*`, `/cdp/webhdfs*`, optional `X-Agent-Key`, and APISIX burst caps.

AMP prefers the same `apache/apisix:3.16.0-debian` image as Compose when `docker` is on the engine. CML application engines usually have no Docker; then `agent-gateway` pins Knox JWKS and runs Spark/Hive/Impala MCP **in-process** (`engine: python`, `mcp: inprocess` on `GET /health`). That avoids CML hairpin HTTPS to sibling app hostnames, which otherwise surfaces as a generic `Internal Server Error`. Sibling MCP apps remain for direct debug URLs. `/cdp/livy_for_spark3*` and `/cdp/webhdfs*` still proxy to Knox.

```text
MCP host → agent-gateway (APISIX, knox-jwt.lua) → mcp-spark|hive|impala → Knox → CDP
```

## Import

The **Configure Project** form and auto-run jobs only happen when you launch from the AMP catalog (or **New Project → ML Prototype**). A plain Git clone does not run `.project-metadata.yaml` and does not show the form.

Workbench project creation **clones git first**. AMP jobs do not run until that clone succeeds. `catalog-entry.yaml` `git_url` is `https://github.com/BrooksIan/CDPAgentGateway.git`. That repo is private today, so an unattended HTTPS clone fails with:

```text
Unable to clone … fatal: could not read Username for 'https://github.com': No such device or address
```

Fix clone access **before** launching the AMP:

1. In GitHub, create a personal access token with `repo` scope (classic) or Contents read on `BrooksIan/CDPAgentGateway` (fine-grained). Do not put the token in git or `catalog-entry.yaml`.
2. In the workbench, add Git credentials for `github.com` (user settings, or site administration if AMP catalog clone uses the workspace credential store). Username is the GitHub user; password is the PAT.
3. Add `catalog-entry.yaml` as a custom AMP source (**Site Administration → AMPs**), or use **New Project → ML Prototype** with this git URL.
4. Open the **CDP Agent Gateway** tile → **Configure Project**. `KNOX_PROXY_URL` must have a **non-empty YAML default** (CML shows `Missing required environment variables` if the default is `null` or blank, even after you type a value). The form is pre-filled from `inventory/cdp.yaml`. Override it for another cluster. Do not add `KNOX_TOKEN` here.
5. Click **Launch Project**. CML then runs tasks in order: install extras, **create and run** Fetch JWKS, **create and run** Smoke-check Knox, start MCP + admin apps, start **agent-gateway** (APISIX).
6. Runtime: Workbench or PBJ Workbench, **Python 3.11 or greater** (3.12 is listed), Standard. No GPU. Do not pick Python 3.10 or older — `requires-python` is `>=3.11`.

A public clone does not need a PAT. Do not encode a token in `git_url`.

Do not put `KNOX_TOKEN` in project env or git. Paste the Knox JWT into the MCP host secret store.

If a Knox job fails (bad `KNOX_PROXY_URL`), later tasks including applications do not start. Fix the project environment, then **Resume** or **Redeploy** ([restart a failed AMP](https://docs.cloudera.com/machine-learning/cloud/applied-ml-prototypes/topics/ml-restart-failed-amp-setup.html)).

If applications exist but stay Starting or Failed, open Application logs. Typical causes:

- Install wrote packages into the session engine instead of `/home/cdsw/.local` (`pip install --user` is required).
- CML applications run in IPython, which already has an asyncio loop. AMP starts uvicorn in a background thread instead of `asyncio.run`.
- `KNOX_PROXY_URL` must be set in **Project Settings → Environment**. An empty value skips JWKS pin; MCP POSTs then fail closed until you set it and rerun Fetch pinned Knox JWKS.
- `GET /health` with `"error": "JSONDecodeError"` means Knox JWKS returned HTML or an empty body (common when the derived URL is `api/v1` and the cluster only serves `api/v2`). Set `KNOX_JWKS_URL` to the inventoried `.../homepage/knoxtoken/api/v2/jwks.json`, keep `UPSTREAM_TLS_VERIFY=false` for lab CAs, push this repo, then **Restart** Agent gateway. A healthy Python edge returns `"engine": "python"`.
- Notebook `MCP HTTP 500` from `agent-gateway` after a valid JWT: check `GET /health` includes `"mcp": "inprocess"`. A body of plain `Internal Server Error` is CML's proxy wrapping an unhandled crash or a hairpin to `mcp-spark.<domain>`; restart Agent gateway after this in-process dispatch change. `gateway_misconfigured` means no Knox PEM.
- The process exited before listening on `127.0.0.1:$CDSW_APP_PORT` (CML probes loopback, not `0.0.0.0`).
- User CPU/memory quota cannot schedule four apps (each 1 CPU / 1 GB). Drop other workloads or raise the quota.
- Static subdomains `mcp-spark`, `mcp-hive`, `mcp-impala`, or `gateway-admin` already exist from a previous AMP attempt.

Push this repo to GitHub, then **Redeploy** the AMP so the workbench re-imports `.project-metadata.yaml`.

## Applications

| Subdomain | CML login | Role |
| --- | --- | --- |
| **`agent-gateway`** | Bypassed | **Agent edge.** Docker APISIX when `docker` exists; otherwise Python (`engine: python`). `/mcp/spark`, `/mcp/hive`, `/mcp/impala`, `/cdp/livy_for_spark3*`, `/cdp/webhdfs*`. Knox JWT + optional `X-Agent-Key`. |
| `mcp-spark` | Bypassed (MCP hosts cannot send CML cookies) | MCP adapter upstream. Direct URL still works (Python JWT). Prefer `agent-gateway`. |
| `mcp-hive` | Bypassed | POST JSON-RPC Hive (read-only). Knox JWT required. `/cdp/hive` is not this app. |
| `mcp-impala` | Bypassed | POST JSON-RPC Impala (read-only). Knox JWT required. `/cdp/impala` is not this app. CDW `HTTP code 401` after a valid JWT is warehouse trust, not APISIX. |
| `gateway-admin` | Required | Operator usage/quotas. Shares `data/gateway.sqlite`. Not an agent route. |

Quotas use sqlite on the project filesystem (`ADMIN_BACKEND=sqlite`). Compose still uses HTTP to the admin container and fails open if that container is down.

Burst cap `MCP_RATE_COUNT` / `MCP_RATE_WINDOW` is enforced in APISIX on `agent-gateway` (same as Compose). Direct MCP app URLs still enforce Python burst limits if used without APISIX.

## MCP host config

The **agent** URL is the APISIX application (POST JSON-RPC, no Streamable HTTP):

```json
{
  "mcpServers": {
    "cdp-spark": {
      "url": "https://agent-gateway.<workspace>/mcp/spark",
      "headers": {
        "Authorization": "Bearer <knox-jwt>",
        "X-Agent-Key": "<partner-key-when-configured>"
      }
    },
    "cdp-hive": {
      "url": "https://agent-gateway.<workspace>/mcp/hive",
      "headers": {
        "Authorization": "Bearer <knox-jwt>",
        "X-Agent-Key": "<partner-key-when-configured>"
      }
    },
    "cdp-impala": {
      "url": "https://agent-gateway.<workspace>/mcp/impala",
      "headers": {
        "Authorization": "Bearer <knox-jwt>",
        "X-Agent-Key": "<partner-key-when-configured>"
      }
    }
  }
}
```

Direct MCP subdomains (`mcp-spark`, etc.) remain for adapter debugging; JWT-only, no caller key unless you also set `AGENT_CALLER_KEY` and route through APISIX.

Previous direct-only config (still valid for debugging):

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
| Agent platform | CML **`agent-gateway`** APISIX app + MCP upstreams (`X-Agent-Key` when `AGENT_CALLER_KEY` is set) |
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
| `7_app-agent-gateway/` | APISIX application (Docker, knox-jwt.lua, same routes as Compose) |
| `examples/agent/third_party_agent.ipynb` | Workbench notebook: simulate a third-party MCP host against the AMP apps |
| `examples/agent/langgraph_agent.ipynb` | Workbench LangGraph ReAct agent over the same MCP tools (needs a model API key in the engine, not project env) |

Paste a Knox JWT in the notebook token cell (`getpass`), or set `KNOX_TOKEN` for that engine only. Do not add `KNOX_TOKEN` or model API keys to project environment or `.project-metadata.yaml`. Override MCP URLs with `MCP_SPARK_URL`, `MCP_HIVE_URL`, or `MCP_IMPALA_URL` if needed. Default is `https://agent-gateway.<CDSW_DOMAIN>/mcp/*`.

## Non-goals

- Docker-in-CML for mock-cdp or full Compose stacks (AMP uses Docker **only** for the APISIX edge container)
- Raw Livy writes on `/cdp/livy_for_spark3*` (GET/HEAD only, same as Compose)
- `/cdp/hive`, `/cdp/impala`, Ozone, NiFi as agent routes
- APISIX `jwt-auth` or a CML model access key as the CDP user
- Streamable HTTP unless a real host fails `initialize`

After a successful workbench proof, set `launchable: true`, add `Launchable (AMP)` to `catalog_classification`, and add Cloudera AI to `product_mapping`. Until then keep `launchable: false`.
