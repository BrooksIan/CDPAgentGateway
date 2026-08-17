from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import httpx

from agentgateway import __version__
from agentgateway.compose import compose_run
from agentgateway.config import write_apisix_config
from agentgateway.doctor import run_checks
from agentgateway.env import admin_url, ensure_dotenv, gateway_url, load_env, upsert_dotenv
from agentgateway.keys import fetch_knox_pubkey, generate_test_keys
from agentgateway.knox import (
    HIVE_ENV_KEYS,
    LOCAL_UPSTREAM,
    SPARK_MCP_PATH,
    SPARK_SESSIONS_PATH,
    agent_paths,
    default_call_path,
    hive_jdbc_updates,
    merge_knox_config,
    parse_knox_proxy_url,
    redact_jdbc,
    require_token_topology,
    spark_livy_path,
    trusted_jku,
)
from agentgateway.hive import HiveError, hive_http_path, show_databases
from agentgateway.mcp import mcp_rpc
from agentgateway.paths import repo_root
from agentgateway.probe import parse_params, request_path
from agentgateway.token import inspect_bearer, knox_claims, public_claims, sign_rs256


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gateway",
        description="Operate the CDP Agent Gateway (local APISIX in front of Knox).",
        epilog=(
            "examples:\n"
            "  gateway init && gateway up\n"
            "  gateway test\n"
            "  gateway token set   # paste JWT on stdin, or: gateway token set eyJ...\n"
            "  gateway token show\n"
            "  gateway knox https://knox.example.com/env/cdp-proxy-token/livy_for_spark3/\n"
            "  gateway jdbc add 'jdbc:hive2://knox.example/;ssl=true;transportMode=http;httpPath=env/cdp-proxy-api/hive'\n"
            "  gateway spark\n"
            "  gateway hive\n"
            "  gateway mcp\n"
            "  gateway admin\n"
            "  gateway fetch-jwks --insecure\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Create .env, mock Knox keys, and APISIX config")
    p_init.add_argument("--force-keys", action="store_true", help="Regenerate local RSA test keys")
    p_init.set_defaults(func=cmd_init)

    sub.add_parser("config", help="Render conf/generated/apisix.yaml").set_defaults(func=cmd_config)

    p_up = sub.add_parser("up", help="Start APISIX + mock CDP + mcp-spark + admin UI")
    p_up.add_argument("--no-build", action="store_true")
    p_up.set_defaults(func=cmd_up)

    sub.add_parser("down", help="Stop the Docker stack").set_defaults(func=cmd_down)

    p_logs = sub.add_parser("logs", help="Show Compose logs")
    p_logs.add_argument("service", nargs="?", default="apisix")
    p_logs.add_argument("-f", "--follow", action="store_true")
    p_logs.add_argument("-n", "--tail", default="100")
    p_logs.set_defaults(func=cmd_logs)

    sub.add_parser("status", help="Compose status and /health").set_defaults(func=cmd_status)

    p_doctor = sub.add_parser("doctor", help="Check laptop prerequisites")
    p_doctor.add_argument("--ping", action="store_true", help="HTTP GET /health")
    p_doctor.set_defaults(func=cmd_doctor)

    p_test = sub.add_parser("test", help="Run pytest")
    p_test.add_argument("--unit", action="store_true", help="Inventory and CLI tests only")
    p_test.add_argument("--live", action="store_true", help="External CDP tests (needs KNOX_TOKEN)")
    p_test.add_argument("pytest_args", nargs="*", help="Extra args after --")
    p_test.set_defaults(func=cmd_test)

    p_token = sub.add_parser("token", help="Set, show, clear, or mint a Knox JWT")
    token_sub = p_token.add_subparsers(dest="token_cmd", required=True)

    p_mint = token_sub.add_parser("mint", help="Mint a mock Knox RS256 JWT (local keys)")
    p_mint.add_argument("--sub", default="analyst")
    p_mint.add_argument("--expires", type=int, default=3600)
    p_mint.set_defaults(func=cmd_token_mint)

    p_set = token_sub.add_parser("set", help="Store a Knox JWT in .env as KNOX_TOKEN")
    p_set.add_argument("token", nargs="?", help="JWT (omit to read from stdin)")
    p_set.add_argument("--fetch-jwks", action="store_true", default=True, help=argparse.SUPPRESS)
    p_set.add_argument("--no-fetch-jwks", action="store_true", help="Do not refresh JWKS from the token jku")
    p_set.add_argument("--insecure", action="store_true", default=True, help=argparse.SUPPRESS)
    p_set.set_defaults(func=cmd_token_set)

    token_sub.add_parser("show", help="Print stored token claims (never the raw JWT)").set_defaults(
        func=cmd_token_show
    )
    token_sub.add_parser("clear", help="Remove KNOX_TOKEN from .env").set_defaults(func=cmd_token_clear)

    p_call = sub.add_parser("call", help="Call a gateway path with a bearer token")
    p_call.add_argument(
        "path",
        nargs="?",
        help=f"Gateway path (default: {SPARK_SESSIONS_PATH})",
    )
    p_call.add_argument("--method", default="GET")
    p_call.add_argument("--mint", action="store_true", help="Sign a local mock token")
    p_call.add_argument("--token", help="Bearer token (default: KNOX_TOKEN or --mint)")
    p_call.add_argument("--sub", default="analyst")
    p_call.add_argument("--param", action="append", default=[], metavar="KEY=VALUE")
    p_call.set_defaults(func=cmd_call)

    p_spark = sub.add_parser("spark", help="Call Livy for Spark 3 through the gateway")
    p_spark.add_argument(
        "resource",
        nargs="?",
        default="sessions",
        help="Livy path after /cdp/livy_for_spark3/ (default: sessions)",
    )
    p_spark.add_argument("--method", default="GET")
    p_spark.add_argument("--mint", action="store_true", help="Sign a local mock token")
    p_spark.add_argument("--token", help="Bearer token (default: KNOX_TOKEN or --mint)")
    p_spark.add_argument("--sub", default="analyst")
    p_spark.add_argument("--param", action="append", default=[], metavar="KEY=VALUE")
    p_spark.set_defaults(func=cmd_spark)

    p_hive = sub.add_parser("hive", help="Run SHOW DATABASES on Knox Hive (token topology, not /cdp/hive)")
    p_hive.add_argument(
        "resource",
        nargs="?",
        default="databases",
        help="Hive probe (default: databases)",
    )
    p_hive.add_argument("--mint", action="store_true", help="Sign a local mock token")
    p_hive.add_argument("--token", help="Bearer token (default: KNOX_TOKEN or --mint)")
    p_hive.add_argument("--sub", default="analyst")
    p_hive.set_defaults(func=cmd_hive)

    p_mcp = sub.add_parser("mcp", help="Call the Spark MCP adapter through the gateway")
    p_mcp.add_argument("--tool", help="tools/call name (default: list tools)")
    p_mcp.add_argument("--arg", action="append", default=[], metavar="KEY=VALUE")
    p_mcp.add_argument("--mint", action="store_true", help="Sign a local mock token")
    p_mcp.add_argument("--token", help="Bearer token (default: KNOX_TOKEN or --mint)")
    p_mcp.add_argument("--sub", default="analyst")
    p_mcp.set_defaults(func=cmd_mcp)

    p_admin = sub.add_parser("admin", help="Show the operator admin UI URL (usage and quotas)")
    p_admin.add_argument("--open", action="store_true", help="Open the UI in the default browser")
    p_admin.set_defaults(func=cmd_admin)

    p_knox = sub.add_parser(
        "knox",
        help="Set or show the CDP Knox proxy URL (writes .env, live mode)",
    )
    p_knox.add_argument(
        "url",
        nargs="?",
        help="Knox HTTPS URL with cdp-proxy-token, preferably .../livy_for_spark3/",
    )
    p_knox.add_argument("--local", action="store_true", help="Reset upstream to the local mock CDP")
    p_knox.add_argument("--show", action="store_true", help="Print the current Knox upstream")
    p_knox.add_argument("--tls-verify", action="store_true", help="Verify Knox TLS (default: off for labs)")
    p_knox.add_argument("--fetch-jwks", action="store_true", help="Download JWKS after setting the URL")
    p_knox.add_argument("--insecure", action="store_true", help="Skip TLS verify when fetching JWKS")
    p_knox.set_defaults(func=cmd_knox)

    p_jdbc = sub.add_parser(
        "jdbc",
        help="Add or show a Hive JDBC URL (Knox HTTP). Does not publish /cdp/hive.",
    )
    jdbc_sub = p_jdbc.add_subparsers(dest="jdbc_cmd", required=True)

    p_jdbc_add = jdbc_sub.add_parser("add", help="Parse jdbc:hive2 HTTP URL and store it in .env")
    p_jdbc_add.add_argument(
        "url",
        nargs="?",
        help="jdbc:hive2://…;transportMode=http;httpPath=…/cdp-proxy-api/hive",
    )
    p_jdbc_add.add_argument("--tls-verify", action="store_true", help="Verify Knox TLS (default: off for labs)")
    p_jdbc_add.add_argument("--fetch-jwks", action="store_true", help="Download JWKS after storing the URL")
    p_jdbc_add.add_argument("--insecure", action="store_true", help="Skip TLS verify when fetching JWKS")
    p_jdbc_add.set_defaults(func=cmd_jdbc_add)

    jdbc_sub.add_parser("show", help="Print stored Hive JDBC fields (passwords redacted)").set_defaults(
        func=cmd_jdbc_show
    )
    jdbc_sub.add_parser("clear", help="Remove stored Hive JDBC from .env").set_defaults(func=cmd_jdbc_clear)

    p_jwks = sub.add_parser("fetch-jwks", help="Download Knox JWKS and write a verifying PEM")
    p_jwks.add_argument("--jwks-url")
    p_jwks.add_argument("--out", default=str(repo_root() / "conf" / "keys" / "knox-live.pem"))
    p_jwks.add_argument("--insecure", action="store_true")
    p_jwks.set_defaults(func=cmd_fetch_jwks)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        return exc.returncode or 1


def cmd_init(args: argparse.Namespace) -> int:
    env_path = ensure_dotenv()
    keys = generate_test_keys(force=args.force_keys)
    generated = write_apisix_config()
    print(f"env {env_path}")
    print(f"keys {keys}")
    print(f"config {generated}")
    return 0


def cmd_config(_args: argparse.Namespace) -> int:
    ensure_dotenv()
    path = write_apisix_config()
    values = load_env()
    print(path)
    print(
        "upstream="
        f"{values['UPSTREAM_SCHEME']}://{values['UPSTREAM_HOST']}:{values['UPSTREAM_PORT']}"
        f"{values['KNOX_PROXY_PREFIX']}"
    )
    return 0


def cmd_up(args: argparse.Namespace) -> int:
    ensure_dotenv()
    generate_test_keys()
    write_apisix_config()
    compose_args = ["up", "-d"]
    if not args.no_build:
        compose_args.append("--build")
    compose_run(compose_args)
    print(f"gateway {gateway_url()}")
    print(f"admin {admin_url()}")
    return 0


def cmd_down(_args: argparse.Namespace) -> int:
    compose_run(["down", "--remove-orphans"])
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    argv = ["logs", "--tail", str(args.tail), args.service]
    if args.follow:
        argv.insert(1, "-f")
    compose_run(argv, check=False)
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    result = compose_run(["ps"], check=False, capture=True)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    try:
        import httpx

        response = httpx.get(f"{gateway_url().rstrip('/')}/health", timeout=2.0)
        print(f"health {response.status_code} {response.text.strip()}")
        admin = httpx.get(f"{admin_url().rstrip('/')}/health", timeout=2.0)
        print(f"admin {admin.status_code} {admin_url()}")
        return 0 if result.returncode == 0 and response.status_code == 200 and admin.status_code == 200 else 1
    except Exception as exc:  # noqa: BLE001
        print(f"health unreachable: {exc}")
        return 1


def cmd_doctor(args: argparse.Namespace) -> int:
    checks = run_checks(ping=args.ping)
    failed = 0
    for check in checks:
        mark = "ok" if check.ok else "FAIL"
        print(f"{mark:4}  {check.name}: {check.detail}")
        if not check.ok:
            failed += 1
    return 1 if failed else 0


def cmd_test(args: argparse.Namespace) -> int:
    pytest_cmd = [sys.executable, "-m", "pytest", "-q"]
    if args.unit:
        pytest_cmd.extend(
            [
                "tests/test_phase0_inventory.py",
                "tests/test_blueprint_standard.py",
                "tests/test_cli.py",
                "tests/test_knox_url.py",
                "tests/test_hive.py",
                "tests/test_mcpspark_livy.py",
                "tests/test_spark_example.py",
                "tests/test_admin_store.py",
                "tests/test_quota_client.py",
                "tests/test_apisix_render.py",
            ]
        )
    else:
        ensure_dotenv()
        generate_test_keys()
        write_apisix_config()
        compose_run(["up", "-d", "--build"])
        pytest_cmd.append("tests")
        pytest_cmd.extend(["-m", "live"] if args.live else ["-m", "not live"])
    pytest_cmd.extend(args.pytest_args)
    return subprocess.call(pytest_cmd, cwd=repo_root())


def cmd_token_mint(args: argparse.Namespace) -> int:
    print(sign_rs256(knox_claims(sub=args.sub, expires_in=args.expires)))
    return 0


def cmd_token_set(args: argparse.Namespace) -> int:
    raw = args.token
    if not raw:
        if sys.stdin.isatty():
            print("Paste the Knox JWT, then EOF (Ctrl-D):", file=sys.stderr)
        raw = sys.stdin.read()
    inspected = inspect_bearer(raw)
    updates = {"KNOX_TOKEN": inspected["token"]}
    claims = public_claims(inspected["payload"], inspected["header"])
    env = load_env()
    jku = claims.get("jku")
    knox_host = env.get("UPSTREAM_HOST")
    if jku and knox_host and knox_host != "mock-cdp":
        updates["KNOX_JWKS_URL"] = trusted_jku(jku, knox_host)
    upsert_dotenv(updates)
    for key, value in claims.items():
        if key != "jku":
            print(f"{key}={value}")
    if "KNOX_JWKS_URL" in updates:
        print(f"KNOX_JWKS_URL={updates['KNOX_JWKS_URL']}")
        if not args.no_fetch_jwks:
            try:
                out = fetch_knox_pubkey(
                    updates["KNOX_JWKS_URL"],
                    repo_root() / "conf" / "keys" / "knox-live.pem",
                    insecure=True,
                )
                print(f"jwks {out}")
                print(f"config {write_apisix_config()}")
            except Exception as exc:  # noqa: BLE001
                print(f"jwks fetch skipped: {exc}")
                print("using existing knox-live.pem if present")
    print("stored KNOX_TOKEN in .env (not printed)")
    return 0


def cmd_token_show(_args: argparse.Namespace) -> int:
    token = load_env().get("KNOX_TOKEN")
    if not token:
        print("error: no KNOX_TOKEN in .env", file=sys.stderr)
        return 1
    inspected = inspect_bearer(token)
    for key, value in public_claims(inspected["payload"], inspected["header"]).items():
        print(f"{key}={value}")
    return 0


def cmd_token_clear(_args: argparse.Namespace) -> int:
    upsert_dotenv({"KNOX_TOKEN": ""})
    print("cleared KNOX_TOKEN")
    return 0


def _bearer_from_args(args: argparse.Namespace) -> str | None:
    if args.mint:
        return sign_rs256(knox_claims(sub=args.sub))
    return args.token or load_env().get("KNOX_TOKEN")


def _issue_call(path: str, token: str, *, method: str, params: list[tuple[str, str]]) -> int:
    print(f"{method.upper()} {path}")
    try:
        response = request_path(path, token=token, method=method, params=params, timeout=60.0)
    except httpx.TimeoutException as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(response.status_code)
    if response.content:
        print(response.text)
    return 0 if response.status_code < 400 else 1


def cmd_call(args: argparse.Namespace) -> int:
    token = _bearer_from_args(args)
    if not token:
        print("error: no token; run gateway token set or pass --mint", file=sys.stderr)
        return 2
    path = args.path or default_call_path(load_env())
    try:
        params = parse_params(args.param)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return _issue_call(path, token, method=args.method, params=params)


def cmd_spark(args: argparse.Namespace) -> int:
    token = _bearer_from_args(args)
    if not token:
        print("error: no token; run gateway token set or pass --mint", file=sys.stderr)
        return 2
    try:
        params = parse_params(args.param)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return _issue_call(spark_livy_path(args.resource), token, method=args.method, params=params)


def cmd_hive(args: argparse.Namespace) -> int:
    token = _bearer_from_args(args)
    if not token:
        print("error: no token; run gateway token set or pass --mint", file=sys.stderr)
        return 2
    resource = (args.resource or "databases").strip().lower()
    if resource not in {"databases", "database", "dbs"}:
        print("error: only `gateway hive databases` is implemented", file=sys.stderr)
        return 2
    env = load_env()
    try:
        print(f"SHOW DATABASES {hive_http_path(env)}")
        names = show_databases(env, token)
    except HiveError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for name in names:
        print(name)
    print(f"count {len(names)}")
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    token = _bearer_from_args(args)
    if not token:
        print("error: no token; run gateway token set or pass --mint", file=sys.stderr)
        return 2
    print(f"POST {SPARK_MCP_PATH}")
    try:
        if args.tool:
            arguments: dict[str, object] = {}
            for key, value in parse_params(args.arg):
                arguments[key] = int(value) if key == "batch_id" and value.isdigit() else value
            response = mcp_rpc("tools/call", token=token, params={"name": args.tool, "arguments": arguments})
        else:
            listed = mcp_rpc("tools/list", token=token)
            print(listed.status_code)
            print(listed.text)
            return 0 if listed.status_code < 400 else 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except httpx.TimeoutException as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(response.status_code)
    if response.content:
        print(response.text)
    return 0 if response.status_code < 400 else 1


def cmd_admin(args: argparse.Namespace) -> int:
    url = admin_url().rstrip("/")
    print(url)
    try:
        response = httpx.get(f"{url}/health", timeout=2.0)
        print(f"health {response.status_code} {response.text.strip()}")
        ok = response.status_code == 200
    except Exception as exc:  # noqa: BLE001
        print(f"health unreachable: {exc}")
        ok = False
    if args.open:
        import webbrowser

        webbrowser.open(url)
    return 0 if ok else 1


def cmd_knox(args: argparse.Namespace) -> int:
    if args.local:
        path = upsert_dotenv(LOCAL_UPSTREAM)
        print(f"wrote {path}")
        _print_knox(load_env())
        try:
            print(f"config {write_apisix_config()}")
        except FileNotFoundError as exc:
            print(f"config skipped: {exc}")
        return 0

    if args.url:
        updates = merge_knox_config(
            load_env(),
            require_token_topology(parse_knox_proxy_url(args.url, tls_verify=args.tls_verify)),
        )
        path = upsert_dotenv(updates)
        print(f"wrote {path}")
        env = load_env()
        _print_knox(env)
        if args.fetch_jwks:
            out = fetch_knox_pubkey(
                env["KNOX_JWKS_URL"],
                repo_root() / "conf" / "keys" / "knox-live.pem",
                insecure=args.insecure or not args.tls_verify,
            )
            print(f"jwks {out}")
        try:
            print(f"config {write_apisix_config()}")
        except FileNotFoundError:
            print("next: gateway fetch-jwks --insecure")
            print("then: gateway up")
        return 0

    _print_knox(load_env())
    return 0


def _print_knox(env: dict[str, str]) -> None:
    keys = (
        "GATEWAY_MODE",
        "KNOX_PROXY_URL",
        "UPSTREAM_SCHEME",
        "UPSTREAM_HOST",
        "UPSTREAM_PORT",
        "KNOX_PROXY_PREFIX",
        "KNOX_SERVICE_PATH",
        "KNOX_SERVICES",
        "KNOX_JWKS_URL",
        "UPSTREAM_TLS_VERIFY",
    )
    for key in keys:
        print(f"{key}={env.get(key, '')}")
    for path in agent_paths(env, gateway_url(env)):
        print(f"agent {path}")
    print(f"mcp {gateway_url(env).rstrip('/')}{SPARK_MCP_PATH}")
    print(f"admin {admin_url(env)}")
    hive_url = env.get("HIVE_KNOX_URL") or ""
    if hive_url:
        print(f"hive {hive_url}")
        print("hive_agent unpublished (/mcp/hive later; /cdp/hive stays 404)")


def cmd_jdbc_add(args: argparse.Namespace) -> int:
    raw = args.url
    if not raw:
        if sys.stdin.isatty():
            print("Paste the Hive JDBC URL, then EOF (Ctrl-D):", file=sys.stderr)
        raw = sys.stdin.read()
    updates = hive_jdbc_updates(load_env(), raw, tls_verify=args.tls_verify)
    path = upsert_dotenv(updates)
    print(f"wrote {path}")
    env = load_env()
    _print_jdbc(env)
    if args.fetch_jwks:
        jwks = env.get("KNOX_JWKS_URL") or ""
        if not jwks:
            print("error: no KNOX_JWKS_URL; run gateway knox <livy-url> first", file=sys.stderr)
            return 2
        out = fetch_knox_pubkey(
            jwks,
            repo_root() / "conf" / "keys" / "knox-live.pem",
            insecure=args.insecure or not args.tls_verify,
        )
        print(f"jwks {out}")
    print("note: Hive JDBC is inventoried only; /cdp/hive stays unpublished")
    return 0


def cmd_jdbc_show(_args: argparse.Namespace) -> int:
    env = load_env()
    if not (env.get("HIVE_JDBC_URL") or "").strip():
        print("error: no Hive JDBC stored; run gateway jdbc add <jdbc:hive2://...>", file=sys.stderr)
        return 1
    _print_jdbc(env)
    return 0


def cmd_jdbc_clear(_args: argparse.Namespace) -> int:
    upsert_dotenv({key: "" for key in HIVE_ENV_KEYS})
    print("cleared Hive JDBC")
    return 0


def _print_jdbc(env: dict[str, str]) -> None:
    jdbc = env.get("HIVE_JDBC_URL") or ""
    print(f"HIVE_JDBC_URL={redact_jdbc(jdbc)}")
    for key in ("HIVE_KNOX_URL", "HIVE_KNOX_PREFIX", "HIVE_KNOX_TOPOLOGY", "HIVE_KNOX_SERVICE"):
        print(f"{key}={env.get(key, '')}")


def cmd_fetch_jwks(args: argparse.Namespace) -> int:
    url = args.jwks_url or load_env().get("KNOX_JWKS_URL")
    if not url:
        print("error: pass --jwks-url or set KNOX_JWKS_URL", file=sys.stderr)
        return 2
    out = fetch_knox_pubkey(url, Path(args.out), insecure=args.insecure)
    print(out)
    return 0
