# Cloudera Blueprint: CDP Agent Gateway

Bring third-party agents to [Cloudera Data Platform](https://www.cloudera.com/) through a north-south gateway. Agents present [Apache Knox](https://knox.apache.org/) JWTs; this gateway never exposes Hive, Impala, Ozone, or NiFi directly. Catalog fields live in [`METADATA.yaml`](METADATA.yaml).

This repo follows the [Cloudera Blueprints Standard](https://github.com/kevinbtalbert/Cloudera-Blueprints-Standard). After reading this page you should know what the blueprint does, who it is for, and how to run the local demo.

## Table of Contents

- [Overview](#overview)
- [Demo](#demo)
- [Use Case](#use-case)
- [Key Features](#key-features)
- [Quickstart](#quickstart)
- [Software Components](#software-components)
- [Target Audience](#target-audience)
- [Repository Structure](#repository-structure)
- [Prerequisites](#prerequisites)
- [Hardware Requirements](#hardware-requirements)
- [Documentation](#documentation)

## Overview

CDP Agent Gateway sits in front of Cloudera Data Platform so Cursor, Claude, and other MCP hosts can call Spark without learning cluster topology. Apache APISIX terminates agent HTTP, validates Knox-issued RS256 JWTs, and forwards the same bearer into Knox `cdp-proxy-token` **Livy for Spark 3**. Knox Trusted Proxy and Apache Ranger remain the authorization source of truth. Hive, Impala, Ozone, and NiFi stay unpublished. Cloudera value is unchanged identity and data policy: agents do not get a parallel credential path into the lakehouse.

## Demo

A recorded Reprise walkthrough is not published yet. The current end-to-end path is the local Docker stack in [Quickstart](#quickstart): mock Knox plus APISIX on `localhost:9080`, Spark MCP at `/mcp/spark`, with pytest covering missing bearer, `alg=none`, expired tokens, and subject forwarding.

## Use Case

Enterprises want third-party coding and analytics agents to run Spark on CDP, but they cannot publish Livy, HiveServer2, Impala, Ozone, or NiFi to those tools. The outcome of this blueprint is a single agent-facing address that allowlists **Livy for Spark 3**, enforces Knox user identity, keeps Ranger in charge of data access, and leaves a path to MCP adapters without replacing the CDP perimeter.

## Key Features

- Knox JWT at the agent edge — no second token format, no APISIX-minted user credentials
- Dual identity — registered agent (caller key or mTLS later) plus CDP user (`sub` / `knox.id`)
- Fail-closed proxy — missing, expired, wrong-issuer, and algorithm-confused tokens never reach Knox
- Spark-only allowlist — Livy for Spark 3 GET/HEAD (`/cdp/livy_for_spark3*`) and MCP (`/mcp/spark`); Hive JDBC is inventoried only (`/cdp/hive` 404)
- Local-to-live path — `gateway knox <url>` points the same Compose file at external Knox
- Ranger stays authoritative — the gateway does not impersonate a different user than the token subject
- Operator admin UI — usage by Knox user and per-user Spark tool quotas on localhost `:9090`

## Quickstart

1. Clone the repository.
2. Install Docker and Python 3.11+ (`make` is optional).
3. Create a virtualenv and install test dependencies:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -e ".[dev]"
   ```

4. Initialize config and start the local stack:

   ```bash
   cp .env.example .env
   gateway init
   gateway up
   gateway test
   ```

   `gateway` is the operator CLI (`python -m agentgateway` also works). Full command list: [docs/operator-cli.md](docs/operator-cli.md). `gateway test` renders APISIX config, starts Compose, and runs pytest (excluding live CDP). `make test` calls the same CLI.

5. Optional: point the same gateway at an external Knox proxy:

   ```bash
   gateway knox https://knox.example.com/env/cdp-proxy-token/livy_for_spark3/
   gateway fetch-jwks --insecure
   gateway up
   ```

   `gateway knox` writes the host, port, `cdp-proxy-token` prefix, and JWKS URL into `.env`. Paste the Knox Livy-for-Spark3 URL. Do not commit `.env`.

Useful local commands:

```bash
gateway doctor --ping
gateway knox https://knox-host/env/cdp-proxy-token/livy_for_spark3/
gateway jdbc add 'jdbc:hive2://knox-host/;ssl=true;transportMode=http;httpPath=env/cdp-proxy-api/hive'
gateway fetch-jwks --insecure
gateway token set          # paste JWT, or: gateway token set eyJ...
gateway token show         # claims only, never the raw JWT
gateway spark              # GET /cdp/livy_for_spark3/sessions
gateway mcp                # list Spark MCP tools
gateway mcp --tool spark_list_batches
gateway mcp --tool spark_submit_batch --arg file=hdfs:///user/you/examples/count_to_10.py
gateway admin              # usage and quotas at http://127.0.0.1:9090
gateway logs -f
gateway down
```

Do not commit `.env`, Knox tokens, or private keys.

## Architecture / Software Components

Agents terminate at APISIX. The `mcp-spark` adapter sits behind the gateway and forwards the caller's Knox bearer to Livy. Knox remains the only hop that presents cluster credentials.

![CDP Agent Gateway traffic path](assets/architecture.svg)

| Component | Role |
| --- | --- |
| Apache APISIX | Agent-facing HTTP edge: TLS, Knox JWT plugin, allowlisted routes, audit |
| `knox-jwt` plugin | Verifies RS256, `iss=KNOXSSO`, expiry; pins JWKS/public key; forwards `Authorization` |
| Mock CDP (lab) | Stand-in Knox JWKS and probe paths for `gateway test` |
| Apache Knox | Token issuance, `cdp-proxy-token`, Trusted Proxy / doAs |
| Apache Ranger | Authorization for the Knox subject on Spark (Livy) |
| MCP adapters | `mcp-spark` Livy tools (list/get/log/submit); not APISIX plugins |
| Operator admin | Local UI on `:9090`: usage by Knox `sub`, daily quotas, audit join by `X-Request-Id` |

Extended design: [docs/architecture.md](docs/architecture.md), [docs/spark.md](docs/spark.md), [docs/hive.md](docs/hive.md), [docs/admin.md](docs/admin.md), and [docs/identity-and-auth.md](docs/identity-and-auth.md).

## Target Audience

- Platform and security architects who must put agents on CDP without a second identity plane
- Data engineers and SEs running a laptop lab against mock Knox or a VPN-connected cluster
- Partner / ISV teams onboarding MCP hosts (Cursor, Claude) behind Knox

## Repository Structure

| Path | Description |
| --- | --- |
| `assets/` | Architecture diagram and catalog media |
| `deploy/` | Docker Compose for local APISIX + mock CDP |
| `docs/` | Architecture, Spark, Hive, identity, phases, inventory, tests |
| `METADATA.yaml` | Catalog metadata for the Cloudera blueprint website |
| `conf/` | APISIX standalone config templates |
| `plugins/` | Custom `knox-jwt` APISIX plugin |
| `inventory/` | Phase 0 CDP inventory consumed by tests |
| `src/agentgateway/` | Operator CLI (`gateway` / `python -m agentgateway`) |
| `scripts/` | Thin wrappers around the CLI for compatibility |
| `tests/` | Phase 0 schema tests and gateway pytest |
| `mcp-spark/` | Livy Spark 3 MCP adapter (upstream of APISIX) |
| `admin/` | Operator usage/quota UI (`localhost:9090`) |
| `examples/spark/` | Sample Spark 3 batch (`count_to_10.py`) |
| `AGENTS.md` | Instructions for coding agents |
| `.cursor/rules/` | Cursor project rules |
| `AgentGateway.code-workspace` | Cursor / VS Code workspace |

Open `AgentGateway.code-workspace` in Cursor so project rules load with the repo.

## Prerequisites

- Git, Docker Desktop (or Engine + Compose v2), Python 3.11+
- `pip install -e ".[dev]"` installs the `gateway` CLI (`make` is optional)
- For the local demo: no CDP entitlement; mock Knox runs in Compose
- For a live cluster: CDP Private Cloud Base or Public Cloud access, VPN or allowlisted laptop, Knox Token API / Token Generation, JWKS URL, and a Ranger-allowed test user
- Secrets belong in `.env` (from `.env.example`), never in git or inventory markdown

## Hardware Requirements

| Deployment | Minimum |
| --- | --- |
| Launchable / demo (local Docker) | 2 CPU, 4 GB RAM, 10 GB disk |
| Production / enterprise (APISIX in front of Knox) | Size APISIX for agent QPS; CDP/Knox/Ranger sizing is unchanged. Plan extra RAM if MCP adapters and long Spark jobs share the same host |

## Documentation

- [Architecture](docs/architecture.md)
- [Working with Spark](docs/spark.md)
- [Operator admin UI](docs/admin.md)
- [Working with Hive](docs/hive.md)
- [Identity and authentication](docs/identity-and-auth.md)
- [Operator CLI](docs/operator-cli.md)
- [Build phases](docs/phases.md)
- [Phase 0 inventory](docs/phase-0-inventory.md)
- [Test cases](docs/testing.md)
- [Agent instructions](AGENTS.md)
- [Cloudera Knox Token API](https://docs.cloudera.com/runtime/7.3.1/knox-authentication/topics/security-knox-token-api.html)
- [APISIX Learning Center](https://apisix.apache.org/learning-center/)
- [API Gateway Authentication](https://apisix.apache.org/learning-center/api-gateway-authentication/)
- [Cloudera Blueprints Standard](https://github.com/kevinbtalbert/Cloudera-Blueprints-Standard)
