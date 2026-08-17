# Deploy

Docker Compose for the laptop demo: Apache APISIX, mock Knox-shaped upstream, `mcp-spark`, `mcp-hive`, `mcp-impala`, and the operator admin UI.

Prefer the operator CLI from the repo root:

```bash
gateway up
gateway down
```

`make up` / `make test` call the same CLI. Compose must use the **repository root** as the project directory so `.env`, `conf/`, and `plugins/` resolve.

Live CDP uses the same file. Prefer `gateway knox <livy-url>` over hand-editing `.env`. Do not put tokens in this directory.

If `docker compose pull` hangs on `docker-credential-desktop get`, pull once with an anonymous Docker config:

```bash
mkdir -p /tmp/docker-anon
printf '%s\n' '{"auths":{},"cliPluginsExtraDirs":["/Applications/Docker.app/Contents/Resources/cli-plugins"]}' > /tmp/docker-anon/config.json
DOCKER_CONFIG=/tmp/docker-anon docker compose --project-directory . -f deploy/docker-compose.yml up -d --build
```
