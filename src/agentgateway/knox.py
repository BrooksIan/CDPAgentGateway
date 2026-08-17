from __future__ import annotations

from urllib.parse import urlparse

TOKEN_TOPOLOGY = "cdp-proxy-token"
API_TOPOLOGY = "cdp-proxy-api"
# Longest names first so cdp-proxy does not match inside cdp-proxy-api.
KNOWN_TOPOLOGIES = (TOKEN_TOPOLOGY, API_TOPOLOGY, "cdp-proxy")
DEFAULT_PREFIX = f"/gateway/{TOKEN_TOPOLOGY}"
JDBC_HIVE2 = "jdbc:hive2://"
JDBC_IMPALA = "jdbc:impala://"
HIVE_SERVICE = "hive"
IMPALA_SERVICE = "impala"
SPARK_LIVY_SERVICE = "livy_for_spark3"
SPARK_SESSIONS_PATH = f"/cdp/{SPARK_LIVY_SERVICE}/sessions"
SPARK_MCP_PATH = "/mcp/spark"
HIVE_MCP_PATH = "/mcp/hive"
IMPALA_MCP_PATH = "/mcp/impala"
HIVE_ENV_KEYS = (
    "HIVE_JDBC_URL",
    "HIVE_KNOX_URL",
    "HIVE_KNOX_PREFIX",
    "HIVE_KNOX_TOPOLOGY",
    "HIVE_KNOX_SERVICE",
)
IMPALA_ENV_KEYS = (
    "IMPALA_JDBC_URL",
    "IMPALA_HOST",
    "IMPALA_PORT",
    "IMPALA_SCHEME",
    "IMPALA_HTTP_PATH",
    "IMPALA_TLS_VERIFY",
)
_MOCK_HOSTS = {"", "mock-cdp", "127.0.0.1", "localhost"}
_JDBC_SECRET_KEYS = {"password", "passwd"}


def impala_warehouse_host(env: dict[str, str]) -> str:
    """CDW Impala coordinator when inventoried; empty means Knox `{prefix}/impala`."""
    host = (env.get("IMPALA_HOST") or "").strip()
    if host in _MOCK_HOSTS:
        return ""
    return host


LOCAL_UPSTREAM = {
    "GATEWAY_MODE": "local",
    "UPSTREAM_SCHEME": "http",
    "UPSTREAM_HOST": "mock-cdp",
    "UPSTREAM_PORT": "8080",
    "UPSTREAM_TLS_VERIFY": "false",
    "KNOX_PROXY_PREFIX": DEFAULT_PREFIX,
    "KNOX_JWKS_URL": "",
    "KNOX_PROXY_URL": "",
    "KNOX_SERVICE_PATH": f"/{SPARK_LIVY_SERVICE}",
    "KNOX_SERVICES": f"/{SPARK_LIVY_SERVICE}",
    "KNOX_TOKEN": "",
    "HIVE_JDBC_URL": "",
    "HIVE_KNOX_URL": "",
    "HIVE_KNOX_PREFIX": "",
    "HIVE_KNOX_TOPOLOGY": "",
    "HIVE_KNOX_SERVICE": "",
    "IMPALA_JDBC_URL": "",
    "IMPALA_HOST": "",
    "IMPALA_PORT": "",
    "IMPALA_SCHEME": "",
    "IMPALA_HTTP_PATH": "",
    "IMPALA_TLS_VERIFY": "",
}


def parse_knox_proxy_url(url: str, *, tls_verify: bool = False) -> dict[str, str]:
    raw = url.strip()
    if not raw:
        raise ValueError("Knox URL is empty")
    if raw.lower().startswith("jdbc:"):
        raw = http_url_from_jdbc(raw)
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"Need an http(s) Knox proxy URL or jdbc:hive2 HTTP URL, got {url!r}")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    prefix = _proxy_prefix(parsed.path)
    service = _service_path(parsed.path, prefix)
    topology = detect_topology(prefix) or TOKEN_TOPOLOGY
    topo_suffix = f"/{topology}"
    jwks_base = prefix[: -len(topo_suffix)] if prefix.endswith(topo_suffix) else prefix
    if not jwks_base:
        jwks_base = "/gateway"

    origin = _origin(parsed.scheme, parsed.hostname, port)
    proxy_url = f"{origin}{prefix}{service}"
    return {
        "GATEWAY_MODE": "live",
        "UPSTREAM_SCHEME": parsed.scheme,
        "UPSTREAM_HOST": parsed.hostname,
        "UPSTREAM_PORT": str(port),
        "UPSTREAM_TLS_VERIFY": "true" if tls_verify else "false",
        "KNOX_PROXY_PREFIX": prefix,
        "KNOX_TOPOLOGY": topology,
        "KNOX_SERVICE_PATH": service,
        "KNOX_SERVICES": service,
        "KNOX_JWKS_URL": f"{origin}{jwks_base}/homepage/knoxtoken/api/v1/jwks.json",
        "KNOX_PROXY_URL": proxy_url,
    }


def http_url_from_jdbc(jdbc: str) -> str:
    raw = jdbc.strip()
    if not raw.lower().startswith(JDBC_HIVE2):
        raise ValueError("Only jdbc:hive2:// URLs can be converted; use HTTPS for other Knox services")
    rest = raw[len(JDBC_HIVE2) :]
    authority_and_path, _, prop_blob = rest.partition(";")
    props = _jdbc_props(prop_blob)
    transport = props.get("transportmode", "binary")
    if transport != "http":
        raise ValueError("Hive JDBC must use transportMode=http so the gateway can proxy Knox HTTP")
    http_path = props.get("httppath", "").strip()
    if not http_path:
        raise ValueError(
            "Hive JDBC needs httpPath pointing at <env>/cdp-proxy-api/hive or "
            "<env>/cdp-proxy-token/hive"
        )
    if not http_path.startswith("/"):
        http_path = "/" + http_path
    if not detect_topology(http_path):
        raise ValueError(
            "Hive JDBC httpPath must include a Knox topology "
            "(cdp-proxy-api or cdp-proxy-token) and hive"
        )
    if _service_name(http_path) != HIVE_SERVICE:
        raise ValueError(f"Hive JDBC httpPath must end with /hive, got {http_path!r}")

    hostport = authority_and_path.split("/", 1)[0]
    if not hostport:
        raise ValueError("Hive JDBC is missing host")
    if ":" in hostport:
        host, port_s = hostport.rsplit(":", 1)
        port = int(port_s)
    else:
        host = hostport
        port = 443 if props.get("ssl", "false").lower() in {"true", "1", "yes"} else 80
    if not host:
        raise ValueError("Hive JDBC is missing host")

    ssl = props.get("ssl", "false").lower() in {"true", "1", "yes"}
    scheme = "https" if ssl else "http"
    return f"{scheme}://{host}:{port}{http_path}"


def parse_hive_jdbc(jdbc: str, *, tls_verify: bool = False) -> dict[str, str]:
    raw = jdbc.strip()
    if not raw.lower().startswith(JDBC_HIVE2):
        raise ValueError("Need a jdbc:hive2:// URL with transportMode=http")
    parsed = parse_knox_proxy_url(raw, tls_verify=tls_verify)
    return {
        "HIVE_JDBC_URL": raw,
        "HIVE_KNOX_URL": parsed["KNOX_PROXY_URL"],
        "HIVE_KNOX_PREFIX": parsed["KNOX_PROXY_PREFIX"],
        "HIVE_KNOX_TOPOLOGY": parsed["KNOX_TOPOLOGY"],
        "HIVE_KNOX_SERVICE": f"/{HIVE_SERVICE}",
        "UPSTREAM_HOST": parsed["UPSTREAM_HOST"],
        "KNOX_JWKS_URL": parsed["KNOX_JWKS_URL"],
        "KNOX_TOPOLOGY": parsed["KNOX_TOPOLOGY"],
    }


def parse_impala_jdbc(jdbc: str, *, tls_verify: bool = False) -> dict[str, str]:
    """Inventory a CDW Impala Virtual Warehouse JDBC URL. Agents still send a Knox JWT."""
    raw = jdbc.strip()
    if not raw.lower().startswith(JDBC_IMPALA):
        raise ValueError("Need a jdbc:impala:// URL with transportMode=http")
    rest = raw[len(JDBC_IMPALA) :]
    authority_and_path, _, prop_blob = rest.partition(";")
    props = _jdbc_props(prop_blob)
    transport = props.get("transportmode", "binary")
    if transport != "http":
        raise ValueError("Impala JDBC must use transportMode=http")
    http_path = (props.get("httppath") or "cliservice").strip()
    if not http_path:
        raise ValueError("Impala JDBC needs httpPath (CDW uses cliservice)")
    http_path = http_path.lstrip("/")
    hostport = authority_and_path.split("/", 1)[0]
    if not hostport:
        raise ValueError("Impala JDBC is missing host")
    if ":" in hostport:
        host, port_s = hostport.rsplit(":", 1)
        port = int(port_s)
    else:
        host = hostport
        port = 443 if props.get("ssl", "false").lower() in {"true", "1", "yes"} else 80
    if not host:
        raise ValueError("Impala JDBC is missing host")
    ssl = props.get("ssl", "false").lower() in {"true", "1", "yes"}
    scheme = "https" if ssl or port == 443 else "http"
    auth = (props.get("auth") or "").strip().lower()
    mech = (props.get("authmech") or "").strip()
    if auth and auth not in {"browser", "jwt"}:
        raise ValueError(f"Unsupported Impala JDBC auth={props.get('auth')!r}; agents use Knox JWT")
    if mech and mech not in {"12"}:
        raise ValueError("Impala JDBC AuthMech must be 12 (JWT); do not store browser passwords")
    return {
        "IMPALA_JDBC_URL": raw,
        "IMPALA_HOST": host,
        "IMPALA_PORT": str(port),
        "IMPALA_SCHEME": scheme,
        "IMPALA_HTTP_PATH": http_path,
        "IMPALA_TLS_VERIFY": "true" if tls_verify else "false",
    }


def impala_jdbc_updates(_existing: dict[str, str], jdbc: str, *, tls_verify: bool = False) -> dict[str, str]:
    parsed = parse_impala_jdbc(jdbc, tls_verify=tls_verify)
    return {key: parsed[key] for key in IMPALA_ENV_KEYS}


def jdbc_inventory_updates(existing: dict[str, str], jdbc: str, *, tls_verify: bool = False) -> dict[str, str]:
    raw = jdbc.strip()
    lowered = raw.lower()
    if lowered.startswith(JDBC_IMPALA):
        return impala_jdbc_updates(existing, raw, tls_verify=tls_verify)
    if lowered.startswith(JDBC_HIVE2):
        return hive_jdbc_updates(existing, raw, tls_verify=tls_verify)
    raise ValueError("Need jdbc:hive2:// (Hive) or jdbc:impala:// (CDW Impala) with transportMode=http")


def hive_jdbc_updates(existing: dict[str, str], jdbc: str, *, tls_verify: bool = False) -> dict[str, str]:
    hive = parse_hive_jdbc(jdbc, tls_verify=tls_verify)
    live_host = (existing.get("UPSTREAM_HOST") or "").strip()
    if existing.get("GATEWAY_MODE") == "live" and live_host and live_host != "mock-cdp":
        if live_host != hive["UPSTREAM_HOST"]:
            raise ValueError(
                f"Hive JDBC host {hive['UPSTREAM_HOST']!r} does not match pinned Knox host {live_host!r}"
            )
    updates = {key: hive[key] for key in HIVE_ENV_KEYS}
    if not (existing.get("KNOX_JWKS_URL") or "").strip():
        updates["KNOX_JWKS_URL"] = hive["KNOX_JWKS_URL"]
    return updates


def redact_jdbc(jdbc: str) -> str:
    parts: list[str] = []
    for part in jdbc.split(";"):
        if "=" not in part:
            parts.append(part)
            continue
        key, value = part.split("=", 1)
        if key.strip().lower() in _JDBC_SECRET_KEYS and value:
            parts.append(f"{key}=***")
        else:
            parts.append(part)
    return ";".join(parts)


def require_token_topology(parsed: dict[str, str]) -> dict[str, str]:
    topology = parsed.get("KNOX_TOPOLOGY") or TOKEN_TOPOLOGY
    if topology != TOKEN_TOPOLOGY:
        raise ValueError(
            f"{topology} URLs are not the Livy token topology. "
            f"Store Hive JDBC with: gateway jdbc add <jdbc:hive2://...>"
        )
    return parsed


def merge_knox_config(existing: dict[str, str], updates: dict[str, str]) -> dict[str, str]:
    merged = dict(updates)
    same_cluster = (
        existing.get("GATEWAY_MODE") == "live"
        and existing.get("UPSTREAM_HOST") == updates.get("UPSTREAM_HOST")
        and existing.get("KNOX_PROXY_PREFIX") == updates.get("KNOX_PROXY_PREFIX")
    )
    services = service_list(updates)
    if same_cluster:
        services = _unique(service_list(existing) + services)
        previous = (existing.get("KNOX_SERVICE_PATH") or "").strip()
        spark = f"/{SPARK_LIVY_SERVICE}"
        if spark in services:
            merged["KNOX_SERVICE_PATH"] = spark
        elif previous:
            merged["KNOX_SERVICE_PATH"] = previous if previous.startswith("/") else f"/{previous}"
        origin = _origin(
            merged["UPSTREAM_SCHEME"],
            merged["UPSTREAM_HOST"],
            int(merged["UPSTREAM_PORT"]),
        )
        merged["KNOX_PROXY_URL"] = f"{origin}{merged['KNOX_PROXY_PREFIX']}"
    merged["KNOX_SERVICES"] = ",".join(services)
    return merged


def service_list(env: dict[str, str]) -> list[str]:
    raw = env.get("KNOX_SERVICES") or env.get("KNOX_SERVICE_PATH") or ""
    items: list[str] = []
    for part in raw.replace(" ", ",").split(","):
        item = part.strip()
        if not item:
            continue
        if not item.startswith("/"):
            item = "/" + item
        item = item.rstrip("/") or "/"
        if item not in items:
            items.append(item)
    return items


def spark_livy_path(resource: str = "sessions") -> str:
    suffix = (resource or "sessions").strip("/")
    if not suffix:
        return f"/cdp/{SPARK_LIVY_SERVICE}/"
    return f"/cdp/{SPARK_LIVY_SERVICE}/{suffix}"


def default_call_path(_env: dict[str, str] | None = None) -> str:
    return SPARK_SESSIONS_PATH


def agent_paths(_env: dict[str, str], gateway: str) -> list[str]:
    return [f"{gateway.rstrip('/')}{SPARK_SESSIONS_PATH}"]


def trusted_jku(jku: str, knox_host: str) -> str:
    parsed = urlparse(jku.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"Token jku is not an http(s) URL: {jku!r}")
    if parsed.hostname != knox_host:
        raise ValueError(
            f"Refusing token jku host {parsed.hostname!r}; expected pinned Knox host {knox_host!r}"
        )
    return jku.strip()


def _jdbc_props(blob: str) -> dict[str, str]:
    props: dict[str, str] = {}
    for part in blob.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        props[key.strip().lower()] = value.strip()
    return props


def _origin(scheme: str, host: str, port: int) -> str:
    origin = f"{scheme}://{host}"
    if (scheme == "https" and port != 443) or (scheme == "http" and port != 80):
        origin = f"{origin}:{port}"
    return origin


def detect_topology(path: str) -> str | None:
    normalized = path or ""
    if normalized and not normalized.startswith("/"):
        normalized = "/" + normalized
    normalized = normalized.rstrip("/") or normalized
    for topology in KNOWN_TOPOLOGIES:
        marker = f"/{topology}"
        idx = normalized.find(marker)
        if idx < 0:
            continue
        end = idx + len(marker)
        if end == len(normalized) or normalized[end] == "/":
            return topology
    return None


def _service_name(path: str) -> str:
    topology = detect_topology(path)
    if not topology:
        return ""
    prefix = _proxy_prefix(path)
    service = _service_path(path if path.startswith("/") else f"/{path}", prefix)
    return service.strip("/").split("/", 1)[0]


def _proxy_prefix(path: str) -> str:
    path = (path or "").rstrip("/")
    if not path:
        return DEFAULT_PREFIX
    if not path.startswith("/"):
        path = "/" + path
    topology = detect_topology(path)
    if topology:
        marker = f"/{topology}"
        idx = path.find(marker)
        return path[: idx + len(marker)]
    if path == "/gateway" or path.startswith("/gateway/"):
        return DEFAULT_PREFIX
    return f"{path}/{TOKEN_TOPOLOGY}"


def _service_path(full_path: str, prefix: str) -> str:
    path = (full_path or "").rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    prefix = prefix.rstrip("/")
    if path == prefix or not path.startswith(prefix + "/"):
        return ""
    return path[len(prefix) :]


def _unique(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen
