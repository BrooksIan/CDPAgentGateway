# Operator CLI

`gateway` (also `ag` and `python -m agentgateway`) operates the laptop APISIX stack. Install with `pip install -e .` or `pip install -r tests/requirements.txt` after `PYTHONPATH=src` / editable install from `pyproject.toml`.

All commands run from the **repository root**. Secrets stay in `.env`.

## Lab

```bash
gateway init          # .env, mock RSA keys, APISIX yaml
gateway up            # docker compose up --build
gateway test          # pytest -m "not live"
gateway doctor --ping
gateway spark --mint  # lab JWT; GATEWAY_MODE=local only
gateway webhdfs ls --mint /
gateway mcp --mint    # list Spark MCP tools (lab)
gateway mcp --adapter hive --mint
gateway mcp --adapter impala --mint
gateway admin         # operator usage/quota/audit UI on :9090
gateway logs -f
gateway down
```

`make test` calls `python -m agentgateway test`. `gateway test` (without `--live`) overlays mock-cdp for APISIX + Compose, runs pytest, then restores yaml from `.env` so a live Knox stack is not rewritten. Recreate APISIX with mock-cdp; a stale APISIX DNS entry makes token-state look like `401 revoked`.

`--mint` signs `conf/keys/private.pem` (`GATEWAY_MODE=local` only). On a live stack the CLI refuses it: APISIX verifies Knox JWKS, so a lab token is `invalid_signature`. Live commands omit `--mint` and use `KNOX_TOKEN` from `gateway token set`. Reset the lab with `gateway knox --local`.

## Live CDP

```bash
gateway knox https://knox.example.com/env/cdp-proxy-token/livy_for_spark3/
gateway jdbc add 'jdbc:hive2://knox.example.cloudera.site/;ssl=true;transportMode=http;httpPath=env/cdp-proxy-api/hive'
gateway jdbc add 'jdbc:impala://coordinator.example:443/default;AuthMech=12;transportMode=http;httpPath=cliservice;ssl=1;auth=browser'
gateway fetch-jwks --insecure
gateway token set                 # paste JWT, or: gateway token set eyJ...
gateway token show                # claims only
gateway up
gateway spark                     # uses KNOX_TOKEN
gateway webhdfs put examples/spark/count_to_10.py /user/you/examples/count_to_10.py
gateway hive                      # SHOW DATABASES via Knox token/hive
gateway mcp                       # list Spark MCP tools
gateway mcp --tool spark_list_batches
gateway mcp --tool spark_submit_batch --arg file=hdfs:///user/you/examples/count_to_10.py --arg name=count-to-10
gateway mcp --adapter hive --tool hive_list_tables --arg database=you
gateway mcp --adapter hive --tool hive_select --arg database=you --arg table=count_to_10 --arg columns=n --arg limit=10
gateway mcp --adapter impala --tool impala_select --arg database=you --arg table=count_to_10 --arg columns=n --arg limit=10
gateway test --live
```

`gateway knox --local` resets upstream to mock CDP. `gateway knox --show` prints the current upstream and the agent URL.

Spark usage: [spark.md](spark.md). Stage `count_to_10.py` with WebHDFS, then submit:

```bash
gateway webhdfs put examples/spark/count_to_10.py /user/you/examples/count_to_10.py
gateway mcp --tool spark_submit_batch --arg file=hdfs:///user/you/examples/count_to_10.py
```

Hive usage after Spark writes Iceberg `{user}.count_to_10`: [hive.md](hive.md), [examples/hive/README.md](../examples/hive/README.md). Impala: [impala.md](impala.md).

```bash
gateway mcp --adapter hive --tool hive_list_tables --arg database=you
gateway mcp --adapter hive --tool hive_describe_table --arg database=you --arg table=count_to_10
gateway mcp --adapter hive --tool hive_select --arg database=you --arg table=count_to_10 --arg columns=n --arg limit=10
gateway mcp --adapter impala --tool impala_select --arg database=you --arg table=count_to_10 --arg columns=n --arg limit=10
```

Hive JDBC is a separate inventory command (`cdp-proxy-api` is not the Livy token topology). CDW Impala JDBC is a third host (`IMPALA_HOST`); it does not overwrite Livy:

```bash
gateway jdbc add 'jdbc:hive2://knox.example.cloudera.site/;ssl=true;transportMode=http;httpPath=env/cdp-proxy-api/hive'
gateway jdbc add 'jdbc:impala://coordinator.example:443/default;AuthMech=12;transportMode=http;httpPath=cliservice;ssl=1;auth=browser'
gateway jdbc show
gateway jdbc clear --adapter impala
```

That stores `HIVE_*` and/or `IMPALA_*` in `.env`. It does **not** publish `/cdp/hive` or `/cdp/impala`. Agents use `/mcp/hive` and `/mcp/impala`. JDBC `auth=browser` is ignored; agents send the Knox JWT (`AuthMech=12`). HTTP `401 invalid_signature` is APISIX; a JSON-RPC tool `status` 401 with `HTTP code 401` is the CDW coordinator rejecting that JWT. Details: [hive.md](hive.md), [impala.md](impala.md#errors).

Knox JWTs (`aud=cdp-proxy-token`) run Hive SQL on the **token** topology. Operator probe (needs `impyla`):

```bash
gateway hive              # SHOW DATABASES
gateway hive databases    # same
```

This talks to Knox `{KNOX_PROXY_PREFIX}/hive` with `KNOX_TOKEN`. It is not an agent route. Install the client with `pip install 'impyla>=0.19'` (or `pip install -e ".[hive]"`). Impala: `gateway impala` uses inventoried `IMPALA_HOST` when set.

## Commands

| Command | Purpose |
| --- | --- |
| `init` | Create `.env`, mock Knox keys, render `conf/generated/apisix.yaml` |
| `config` | Re-render APISIX yaml from `.env` |
| `up` / `down` | Start/stop `deploy/docker-compose.yml` |
| `logs [-f] [service]` | Compose logs (default `apisix`) |
| `status` | Compose `ps` plus `GET /health` |
| `doctor [--ping]` | Docker, Python, `.env`, optional health |
| `test [--unit\|--live]` | pytest. Default: mock-cdp overlay, Compose `--force-recreate`, then restore `.env` yaml |
| `knox [url]` | Write live Knox token-topology upstream into `.env` |
| `jdbc add\|show\|clear` | Store Hive Knox JDBC and/or CDW Impala JDBC; does not publish `/cdp/hive` or `/cdp/impala` |
| `fetch-jwks` | Download JWKS → `conf/keys/knox-live.pem` |
| `token mint\|set\|show\|clear` | Lab mint (`token mint` / `--mint` is local only); store, inspect, or drop `KNOX_TOKEN` |
| `spark [resource]` | `GET /cdp/livy_for_spark3/<resource>` (default `sessions`). Writes are MCP-only. |
| `webhdfs ls\|stat\|mkdir\|put` | Knox WebHDFS through `/cdp/webhdfs` (JWT). Stage Spark `file` URIs. No `DELETE`. |
| `hive [databases]` | `SHOW DATABASES` on Knox `{prefix}/hive` (JWT; not `/cdp/hive`) |
| `impala [databases]` | `SHOW DATABASES` on CDW `IMPALA_HOST` or Knox `{prefix}/impala` (JWT; not `/cdp/impala`) |
| `mcp [--adapter spark\|hive\|impala] [--tool NAME]` | JSON-RPC to `/mcp/spark`, `/mcp/hive`, or `/mcp/impala`. Sends `X-Agent-Key` when `AGENT_CALLER_KEY` is set. `--mint` is lab-only. |
| `admin [--open]` | Operator usage/quota/audit UI at `http://127.0.0.1:9090` (not an agent route) |
| `call [path]` | Call an arbitrary gateway path with a bearer |

Optional Cloudera AI Workbench packaging is not a `gateway` subcommand. See [amp.md](amp.md).

## Docker credential hang

If `docker compose pull` blocks on `docker-credential-desktop get`, see [deploy/README.md](../deploy/README.md) for an anonymous `DOCKER_CONFIG` workaround.
