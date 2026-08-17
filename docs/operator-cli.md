# Operator CLI

`gateway` (also `ag` and `python -m agentgateway`) operates the laptop APISIX stack. Install with `pip install -e .` or `pip install -r tests/requirements.txt` after `PYTHONPATH=src` / editable install from `pyproject.toml`.

All commands run from the **repository root**. Secrets stay in `.env`.

## Lab

```bash
gateway init          # .env, mock RSA keys, APISIX yaml
gateway up            # docker compose up --build
gateway test          # pytest -m "not live"
gateway doctor --ping
gateway spark --mint  # GET /cdp/livy_for_spark3/sessions with a mock JWT
gateway mcp --mint    # list Spark MCP tools
gateway admin         # operator usage/quota/audit UI on :9090
gateway logs -f
gateway down
```

`make test` calls `python -m agentgateway test`.

## Live CDP

```bash
gateway knox https://knox.example.com/env/cdp-proxy-token/livy_for_spark3/
gateway jdbc add 'jdbc:hive2://knox.example.cloudera.site/;ssl=true;transportMode=http;httpPath=env/cdp-proxy-api/hive'
gateway fetch-jwks --insecure
gateway token set                 # paste JWT, or: gateway token set eyJ...
gateway token show                # claims only
gateway up
gateway spark                     # uses KNOX_TOKEN
gateway hive                      # SHOW DATABASES via Knox token/hive
gateway mcp                       # list Spark MCP tools
gateway mcp --tool spark_list_batches
gateway mcp --tool spark_submit_batch --arg file=hdfs:///user/you/examples/count_to_10.py
gateway test --live
```

`gateway knox --local` resets upstream to mock CDP. `gateway knox --show` prints the current upstream and the agent URL.

Spark usage: [spark.md](spark.md). Hive JDBC is a separate inventory command (`cdp-proxy-api` is not the Livy token topology):

```bash
gateway jdbc add 'jdbc:hive2://knox.example.cloudera.site/;ssl=true;transportMode=http;httpPath=env/cdp-proxy-api/hive'
gateway jdbc show
gateway jdbc clear
```

That stores `HIVE_*` in `.env` for later `mcp-hive`. It does **not** publish `/cdp/hive`. Details: [hive.md](hive.md). `gateway knox` still pins `cdp-proxy-token` (Livy). `gateway knox` with a `cdp-proxy-api` URL is rejected and points at `gateway jdbc add`.

Knox JWTs (`aud=cdp-proxy-token`) run Hive SQL on the **token** topology. Operator probe (needs `impyla`):

```bash
gateway hive              # SHOW DATABASES
gateway hive databases    # same
```

This talks to Knox `{KNOX_PROXY_PREFIX}/hive` with `KNOX_TOKEN`. It is not an agent route. Install the client with `pip install 'impyla>=0.19'` (or `pip install -e ".[hive]"`).

## Commands

| Command | Purpose |
| --- | --- |
| `init` | Create `.env`, mock Knox keys, render `conf/generated/apisix.yaml` |
| `config` | Re-render APISIX yaml from `.env` |
| `up` / `down` | Start/stop `deploy/docker-compose.yml` |
| `logs [-f] [service]` | Compose logs (default `apisix`) |
| `status` | Compose `ps` plus `GET /health` |
| `doctor [--ping]` | Docker, Python, `.env`, optional health |
| `test [--unit\|--live]` | pytest; default starts Compose |
| `knox [url]` | Write live Knox token-topology upstream into `.env` |
| `jdbc add\|show\|clear` | Store Hive JDBC (`cdp-proxy-api` or token); does not publish `/cdp/hive` |
| `fetch-jwks` | Download JWKS → `conf/keys/knox-live.pem` |
| `token mint\|set\|show\|clear` | Mock mint, store, inspect, or drop `KNOX_TOKEN` |
| `spark [resource]` | `GET /cdp/livy_for_spark3/<resource>` (default `sessions`). Writes are MCP-only. |
| `hive [databases]` | `SHOW DATABASES` on Knox `{prefix}/hive` (JWT; not `/cdp/hive`) |
| `mcp [--tool NAME]` | JSON-RPC to `/mcp/spark` (`tools/list` or `tools/call`) |
| `admin [--open]` | Operator usage/quota/audit UI at `http://127.0.0.1:9090` (not an agent route) |
| `call [path]` | Call an arbitrary gateway path with a bearer |

Optional Cloudera AI Workbench packaging is not a `gateway` subcommand. See [amp.md](amp.md).

## Docker credential hang

If `docker compose pull` blocks on `docker-credential-desktop get`, see [deploy/README.md](../deploy/README.md) for an anonymous `DOCKER_CONFIG` workaround.
