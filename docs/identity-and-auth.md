# Identity and authentication

A Cursor or Claude workspace key is not a CDP user. Every request must bind **both** a registered agent and a Knox subject. Ranger authorizes the subject. Phase 1 curl/CLI tests are JWT-only; agent-product identity (mTLS or caller key) is Phase 3.

## Principals

| Principal | Credential | Enforced by | Purpose |
| --- | --- | --- | --- |
| End user | Knox JWT (`sub`, `knox.id`) | APISIX `knox-jwt` or AMP Python `knox-jwt`, then Knox | Who is acting on CDP data |
| Agent platform | mTLS or caller key | APISIX consumer (not yet) | Which third-party product is calling |
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
| Key Auth | Phase 3 layer | Identify the agent product, never the CDP user |
| mTLS | Phase 3 for partners | Machine identity for registered agent platforms |
| OAuth 2.0 / OIDC code flow | Phase 3 | MCP hosts expect RFC 9728 + PKCE |
| HMAC / Basic / LDAP | No at agent edge | Knox already handles directory auth when minting tokens |

`hide_credentials` is **false**: the caller bearer is forwarded to Knox. The plugin never logs the raw token. Failures return `401` with `WWW-Authenticate: Bearer realm="knox"` and `X-Agent-Gateway-Reason`.

## Gaps to plan for

**Revocation.** Managed Knox tokens can be disabled while still unexpired. Local JWT validation will accept them until `exp`. Phase 1 uses short TTL. Phase 3 adds a Knox token-state check.

**JWKS pinning.** `gateway token set` and `trusted_jku()` honor `jku` only when the host matches `UPSTREAM_HOST`. Never fetch keys from an arbitrary URL in the token. APISIX itself verifies a PEM copied to `conf/generated/knox-public.pem`.

**MCP OAuth.** Cursor, Claude, and VS Code expect protected-resource metadata, `401 WWW-Authenticate`, and PKCE. Phase 1 uses a pasted Knox bearer (`gateway token set`). That is a private preview, not unmanaged third-party onboarding.

## Phase 1 token path

1. Operator obtains a Knox JWT from Token Generation or Token API v2.
2. `gateway knox https://…/cdp-proxy-token/livy_for_spark3/` writes the live upstream.
3. `gateway token set` stores the JWT in `.env` as `KNOX_TOKEN` (never printed) and refreshes JWKS from a pinned `jku` when present.
4. `gateway up` then `gateway spark`, `gateway webhdfs`, or `gateway mcp` (or an MCP host) sends `Authorization: Bearer` to `http://127.0.0.1:9080`.
5. APISIX validates signature, `iss`, `sub`, and `exp`, then forwards the same header to Knox.

## Audit

APISIX assigns `X-Request-Id` on every route. `mcp-spark` records tool name, Knox `sub`, and `knox.id` (`X-Knox-Token-Id`) against that id in the operator sqlite store. Lookup:

```bash
curl -s "http://127.0.0.1:9090/api/audit?request_id=<X-Request-Id>"
```

The join is adapter-side, not a Livy daemon log ingest. The raw bearer is never stored or logged.
