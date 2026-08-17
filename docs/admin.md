# Operator admin UI

The admin console is for **gateway operators**, not MCP hosts. It is bound to `127.0.0.1:9090` and is **not** published on APISIX `:9080`. Agents must keep using `/mcp/spark`.

Open after `gateway up`:

```bash
gateway admin
gateway admin --open
```

Default URL: `http://127.0.0.1:9090`.

## What it shows

- Usage for the current **UTC day**, keyed by Knox `sub` (and `knox.id` when forwarded)
- Tool calls, Spark submits, quota denials, and errors
- Audit join: tool + `sub` + `knox.id` + APISIX `X-Request-Id`. Bearers are never stored.

Lookup a call from the UI or:

```bash
curl -s "http://127.0.0.1:9090/api/audit?request_id=<X-Request-Id>"
```

## Quotas

Empty fields mean unlimited. Per-user rows override the default `*` quota.

| Field | Applies to |
| --- | --- |
| Daily tool calls | Every `tools/call` (list, get, log, submit) |
| Daily submits | `spark_submit_batch` only |

`mcp-spark` checks quotas **before** Livy. A denied submit returns an MCP tool error (`status=429`) and does not reach Knox. Ranger still authorizes data for allowed calls.

If the admin service is down, `mcp-spark` **fails open** (allows the call) so a laptop UI outage does not brick Spark. Restart `admin` to enforce again.

APISIX also caps **bursts** on `/mcp/spark` (`limit-count`, keyed by Knox `sub`). Default `MCP_RATE_COUNT=60` per `MCP_RATE_WINDOW=60` seconds. That is HTTP `429` at the edge, before the adapter. Daily quotas above are a different layer. Livy GET on `/cdp/livy_for_spark3*` is not burst-capped.

## What it is not

- Not Ranger and not a CDP user directory
- Not TLS, mTLS, or partner caller keys (Phase 3)
- Not an agent route (`GET /admin` on `:9080` is 404)
