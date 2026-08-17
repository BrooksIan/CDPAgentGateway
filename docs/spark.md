# Working with Spark

Agents reach Spark only through this gateway. They never call Livy or Knox hostnames directly.

Two surfaces share the same Knox JWT and the same Ranger subject:

| Surface | URI | Methods | Who uses it |
| --- | --- | --- | --- |
| Livy HTTP | `/cdp/livy_for_spark3*` | GET, HEAD | Operators (`gateway spark`), tests |
| MCP | `/mcp/spark` | POST (JSON-RPC) | Cursor, Claude, `gateway mcp` |

Both require `Authorization: Bearer <knox-jwt>`. APISIX `knox-jwt` validates the token, then either rewrites to Knox Livy or forwards to the `mcp-spark` adapter. Hive and other CDP paths stay **404**.

```
agent / CLI  →  APISIX  →  mcp-spark (MCP only)  →  Knox cdp-proxy-token  →  Livy for Spark 3
```

## Lab

```bash
gateway init
gateway up
gateway spark --mint
gateway mcp --mint
```

Mock Livy lives in `mock-cdp`. `gateway spark --mint` signs a local RS256 JWT. No CDP entitlement is required.

## Live cluster

```bash
gateway knox https://knox.example.com/<env>/cdp-proxy-token/livy_for_spark3/
gateway fetch-jwks --insecure
gateway token set
gateway up
gateway spark
gateway mcp
```

`gateway knox` must see topology `cdp-proxy-token` and service `livy_for_spark3`. A `cdp-proxy-api` URL is rejected; that topology is for [Hive JDBC inventory](hive.md).

The Knox subject (`sub`) must be allowed by Ranger to use Livy / the Spark queue. The gateway does not impersonate (`proxyUser` is rejected on submit).

## Livy HTTP

`gateway spark [resource]` is `GET /cdp/livy_for_spark3/<resource>` (default `sessions`).

```bash
gateway spark                 # GET .../sessions
gateway spark batches         # GET .../batches
gateway spark batches/0       # GET .../batches/0
```

Rewrite: `/cdp/(.*)` → `{KNOX_PROXY_PREFIX}/$1`. Example:

`GET http://127.0.0.1:9080/cdp/livy_for_spark3/sessions`  
→ `GET https://knox…/<prefix>/cdp-proxy-token/livy_for_spark3/sessions`

Prefer MCP for agents. Raw Livy on the agent listener is **GET/HEAD only**. `POST`/`PUT`/`DELETE` (including `.../sessions/{id}/statements` and `POST .../batches`) return 404 or 405. Submit a cluster file URI with `spark_submit_batch`.

## MCP tools

`mcp-spark` is a Compose upstream. APISIX strips `/mcp/spark` to `/mcp` on that service. Tools forward the **caller** bearer; they hold no cluster secrets.

| Tool | Livy call | Notes |
| --- | --- | --- |
| `spark_list_sessions` | `GET /sessions` | Lists truncated to 25 |
| `spark_list_batches` | `GET /batches` | Same cap |
| `spark_get_batch` | `GET /batches/{id}` | `id`, `state`, `appId`, owner only |
| `spark_get_log` | `GET /batches/{id}/log` | Last 80 lines, 8k chars |
| `spark_submit_batch` | `POST /batches` | **Write** as Knox `sub`. HDFS/object-store `file` only |

```bash
gateway mcp
gateway mcp --tool spark_list_batches
gateway mcp --tool spark_get_batch --arg batch_id=0
gateway mcp --tool spark_get_log --arg batch_id=0
```

### Submit (write)

This tool runs Spark **as the Knox token subject**. Ranger still decides whether that user may use the queue. Copy a job to a URI Ranger allows, then submit that URI. Livy cannot read a laptop path.

Allowed `file` schemes: `hdfs`, `viewfs`, `s3a`, `s3`, `abfs`, `abfss`, `o3fs`, `ofs`. Rejected: `http`, `file` (unless `SPARK_ALLOW_FILE_SCHEME=true` for a closed lab), `..` in the path, inline `code`, `proxyUser`.

Sample job: [`examples/spark/count_to_10.py`](../examples/spark/count_to_10.py).

```bash
hdfs dfs -mkdir -p /user/$USER/examples
hdfs dfs -put -f examples/spark/count_to_10.py /user/$USER/examples/count_to_10.py

gateway mcp --tool spark_submit_batch \
  --arg file=hdfs:///user/$USER/examples/count_to_10.py \
  --arg name=count-to-10
```

Poll with `spark_get_batch` until `state` is `success` or `dead`. Then `spark_get_log`.

Do not expose interactive Livy `POST /sessions/{id}/statements` (run code). That is how prompt injection becomes a cluster shell. APISIX does not match that method on `/cdp/livy_for_spark3*`; mcp-spark has no statements tool.

## MCP host config

Put the Knox JWT in the host secret store, never in git.

```json
{
  "mcpServers": {
    "cdp-spark": {
      "url": "http://127.0.0.1:9080/mcp/spark",
      "headers": {
        "Authorization": "Bearer <knox-jwt>"
      }
    }
  }
}
```

Agents should call `/mcp/spark` only. Do not teach them the Knox URL, `/cdp/hive`, or the operator admin UI (`:9090`).

Operators set per-user daily call/submit quotas in [admin.md](admin.md). A denied submit is an MCP tool error and does not reach Livy. Operators look up a call by APISIX `X-Request-Id` (`GET /api/audit`) to join tool, `sub`, and `knox.id`.

## What Spark does not do

- No Hive SQL (see [hive.md](hive.md))
- No catch-all `/cdp/*`
- No raw Livy writes on `/cdp/livy_for_spark3*` (GET/HEAD only)
- No gateway-held keytab or impersonation
- No full executor log dump into an LLM context
