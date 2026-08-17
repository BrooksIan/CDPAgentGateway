# Identity and authentication

A Cursor or Claude workspace key is not a CDP user. Every MCP request must bind **both** a registered agent (`X-Agent-Key` on Compose) and a Knox subject. Ranger authorizes the subject. Operator Livy GET / WebHDFS and the AMP profile are JWT-only for the agent product (CML project identity on AMP).

## Principals

| Principal | Credential | Enforced by | Purpose |
| --- | --- | --- | --- |
| End user | Knox JWT (`sub`, `knox.id`) | APISIX `knox-jwt` or AMP Python `knox-jwt`, then Knox | Who is acting on CDP data |
| Agent platform | `X-Agent-Key` (Compose MCP) or CML project (AMP) | APISIX `key-auth` on `/mcp/*`; not yet on AMP | Which third-party product is calling |
| Tool / service | Knox topology + Ranger | Knox + Ranger | What that user may read or write |

## Knox token facts

CDP issues managed JWTs from the Knox Token API. Agents send `Authorization: Bearer`. APISIX verifies Knox's signature and claims, then passes the **same** bearer to `cdp-proxy-token`.

| Field | Typical Knox value | Gateway action |
| --- | --- | --- |
| `alg` | RS256 | Reject anything else (`invalid_alg`) |
| `iss` | `KNOXSSO` | Exact match (`invalid_issuer`) |
| `sub` | CDP username | Required; forwarded as `X-Knox-User` |
| `knox.id` | Token UUID | Forwarded as `X-Knox-Token-Id` when present |
| `managed.token` | `true` | Expect revocation; do not trust `exp` alone |
| `jku` | `knoxtoken/.../jwks.json` | CLI fetches JWKS only from the **configured** Knox host |
| `exp` | Default TTL ~1 hour | Enforce with `KNOX_CLOCK_SKEW` (default 60s) |
| `knox.groups` | Optional | Coarse tool allowlists only (not enforced yet) |

Prefer the JWT bearer over Knox passcode tokens. If a passcode must be exchanged, do it at the gateway and never log the value. `gateway token show` prints claims only.

## What this repo actually uses

The [APISIX authentication guide](https://apisix.apache.org/learning-center/api-gateway-authentication/) is right about JWT at the edge, short TTLs, and audit logs. Knox has no OIDC discovery document, and APISIX `jwt-auth` mints its own consumer tokens.

| Method | Use here? | Why |
| --- | --- | --- |
| `plugins/knox-jwt.lua` | **Yes — primary on Compose** | RS256 + `iss` + `exp` + `sub` against a pinned PEM |
| `agentgateway.knox_jwt` | **Yes — AMP profile only** | Same fail-closed reasons as the Lua plugin; does not follow `jku` |
| `jwt-auth` | No | Issues APISIX consumer JWTs |
| `openid-connect` `bearer_only` | No | Knox is not a full OIDC provider |
| Key Auth | **Yes — MCP routes on Compose** | `X-Agent-Key` names the agent product (`AGENT_CALLER_KEY`). Empty key disables the plugin. Never a CDP user. |
| mTLS | After HTTPS | Machine identity for registered agent platforms leaving the VPN |
| OAuth 2.0 / OIDC code flow | PRM only | RFC 9728 metadata is published; PKCE broker waits on an IdP that can exchange into a Knox JWT |
| HMAC / Basic / LDAP | No at agent edge | Knox already handles directory auth when minting tokens |

`hide_credentials` is **false** for the Knox bearer: the caller JWT is forwarded to Knox. The agent caller key uses `hide_credentials: true` so Knox never sees it. The plugin never logs the raw token. Failures return `401` with `WWW-Authenticate: Bearer realm="knox", resource_metadata="…/.well-known/oauth-protected-resource"` and `X-Agent-Gateway-Reason`.

Public metadata (no JWT):

```bash
curl -s http://127.0.0.1:9080/.well-known/oauth-protected-resource
```

`authorization_servers` is empty unless `KNOX_AUTHORIZATION_SERVER` is set. MCP hosts keep using a pasted Knox bearer (`gateway token set`) plus `X-Agent-Key` until an IdP can exchange into Knox.

## Gaps to plan for

**Revocation.** Managed Knox tokens can be disabled while still unexpired. Local JWT validation used to accept them until `exp`. The gateway now calls a host-pinned token-state URL with `knox.id` (`401` `revoked`). Local mock: `http://mock-cdp:8080/gateway/homepage/knoxtoken/api/v2/token/state/{id}`. Live: set `KNOX_TOKEN_STATE_URL` on the same host as `UPSTREAM_HOST`, or leave unset (signature + `exp` only).

**JWKS pinning.** `gateway token set` and `trusted_jku()` honor `jku` only when the host matches `UPSTREAM_HOST`. Never fetch keys from an arbitrary URL in the token. APISIX itself verifies a PEM copied to `conf/generated/knox-public.pem`. Token-state URLs use the same host pin.

**Lab `--mint` vs live Knox.** `gateway … --mint` and `gateway token mint` sign `conf/keys/private.pem`. That PEM is what APISIX loads when `GATEWAY_MODE=local`. Live mode copies Knox JWKS into `knox-public.pem`, so a minted token is `invalid_signature`. The CLI refuses `--mint` in live mode; use `KNOX_TOKEN` from `gateway token set`.

**MCP OAuth.** Cursor, Claude, and VS Code expect protected-resource metadata, `401 WWW-Authenticate`, and PKCE. PRM and `resource_metadata` are published. PKCE token exchange is not: Knox is not an OIDC authorization server. Do not mint a second user token format.

## Phase 1 token path

1. Operator obtains a Knox JWT from Token Generation or Token API v2.
2. `gateway knox https://…/cdp-proxy-token/livy_for_spark3/` writes the live upstream.
3. `gateway token set` stores the JWT in `.env` as `KNOX_TOKEN` (never printed) and refreshes JWKS from a pinned `jku` when present.
4. `gateway up` then `gateway spark`, `gateway webhdfs`, `gateway mcp`, `gateway mcp --adapter hive`, `gateway mcp --adapter impala`, or an MCP host sends `Authorization: Bearer` to `http://127.0.0.1:9080`. MCP also sends `X-Agent-Key: $AGENT_CALLER_KEY` (default `lab-agent`). Do not pass `--mint` on the live path.
5. APISIX validates signature, `iss`, `sub`, and `exp`, then forwards the same header to Knox.

## Audit

APISIX assigns `X-Request-Id` on every route. `mcp-spark`, `mcp-hive`, and `mcp-impala` record tool name, Knox `sub`, and `knox.id` (`X-Knox-Token-Id`) against that id in the operator sqlite store. Lookup:

```bash
curl -s "http://127.0.0.1:9090/api/audit?request_id=<X-Request-Id>"
```

The join is adapter-side, not a Livy daemon log ingest. The raw bearer is never stored or logged.
