# Operator admin UI

The admin console is for **gateway operators**, not MCP hosts. It is bound to `127.0.0.1:9090` and is **not** published on APISIX `:9080`. Agents must keep using `/mcp/spark`.

Open after `gateway up`:

```bash
gateway admin
gateway admin --open
```

Default URL: `http://127.0.0.1:9090`.

On the optional CML AMP profile the same UI is a workbench application (`gateway-admin`) with **CML login**. It shares `data/gateway.sqlite` with mcp-spark. It is still not an agent route. How-to: [amp.md](amp.md).

![Operator console: path status, health, and UTC-day usage](../assets/admin-overview.png)

## What it shows

- Path status: quotas enforcing, APISIX burst cap, `GATEWAY_MODE`, upstream host, health of admin / mcp-spark / APISIX
- Usage for a chosen **UTC day**, keyed by Knox `sub` (and `knox.id` when forwarded)
- Default `*` quota plus per-user overrides
- Activity filtered by user, tool, and result (ok / quota 429 / Livy error)
- Audit join: click a request id (or lookup) for tool + `sub` + `knox.id` + `X-Request-Id`. Bearers are never stored.

```bash
curl -s "http://127.0.0.1:9090/api/audit?request_id=<X-Request-Id>"
curl -s "http://127.0.0.1:9090/api/status"
```

## Two 429s

| Layer | Where | In this sqlite? |
| --- | --- | --- |
| Daily quota | `mcp-spark` calls admin **before** Livy | Yes (`kind=denied`) |
| Burst cap | APISIX `limit-count` on `/mcp/spark` (`MCP_RATE_COUNT` / `MCP_RATE_WINDOW`); AMP uses the same env on the Python JWT middleware | No |

If this admin service is down, `mcp-spark` **fails open** (allows the call). The UI badge is honest: quotas are enforcing only while you can load this page.

Livy GET on `/cdp/livy_for_spark3*` and WebHDFS on `/cdp/webhdfs*` are not burst-capped. Ranger still authorizes data for allowed calls.

## Quotas

Empty fields mean unlimited. Per-user rows override the default `*` quota.

![Default `*` quota and a per-user override for `analyst`](../assets/admin-quotas.png)

| Field | Applies to |
| --- | --- |
| Daily tool calls | Every `tools/call` (list, get, log, submit) |
| Daily submits | `spark_submit_batch` only |

A denied submit returns an MCP tool error (`status=429`) and does not reach Knox.

## Usage and audit

Usage is per UTC day, keyed by Knox `sub`. Click a request id in Activity (or paste it into Audit join) to see tool, `sub`, and `knox.id` for that call. Bearers are never stored.

![UTC-day usage by Knox `sub`, plus audit lookup by `X-Request-Id`](../assets/admin-usage-audit.png)

![Activity log: tool calls keyed by Knox user and request id](../assets/admin-activity.png)

## What it is not

- Not Ranger and not a CDP user directory
- Not TLS, mTLS, or partner caller keys (Phase 3)
- Not an agent route (`GET /admin` on `:9080` is 404)
- Not a Livy / Cloudera Manager console (no job kill, no cluster metrics, no JWT paste)
