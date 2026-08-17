"""Operator WebHDFS through the gateway. Forwards the caller's Knox JWT; never logs it."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlunparse

import httpx

from agentgateway.env import gateway_url, load_env

REQUEST_TIMEOUT = 60.0
WEBHDFS_V1 = "/cdp/webhdfs/v1"


class WebHdfsError(Exception):
    pass


def webhdfs_path(hdfs_path: str) -> str:
    normalized = (hdfs_path or "/").strip() or "/"
    if normalized.startswith("hdfs://"):
        parsed = urlparse(normalized)
        normalized = parsed.path or "/"
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    return f"{WEBHDFS_V1}{normalized}"


def gateway_create_location(location: str, env: dict[str, str] | None = None) -> str:
    """Rewrite a Knox CREATE Location onto this gateway. Refuse foreign hosts."""
    raw = (location or "").strip()
    if not raw:
        raise WebHdfsError("CREATE returned no Location")
    parsed = urlparse(raw)
    env = env or load_env()
    knox_host = (env.get("UPSTREAM_HOST") or "").strip()
    prefix = (env.get("KNOX_PROXY_PREFIX") or "").rstrip("/")
    gateway = gateway_url().rstrip("/")
    gateway_host = urlparse(gateway).hostname
    host = parsed.hostname or ""
    allowed = {knox_host, gateway_host, "127.0.0.1", "localhost"} - {""}
    if host not in allowed:
        raise WebHdfsError(f"refusing redirect host {host!r}; expected pinned Knox host {knox_host!r}")
    path = parsed.path or ""
    if prefix and path.startswith(prefix + "/"):
        path = "/cdp" + path[len(prefix) :]
    elif path.startswith("/webhdfs/"):
        path = "/cdp" + path
    elif not path.startswith("/cdp/webhdfs"):
        raise WebHdfsError("CREATE Location is not a WebHDFS data path")
    gw = urlparse(gateway)
    return urlunparse((gw.scheme or "http", gw.netloc, path, "", parsed.query, ""))


def _headers(token: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    if not token:
        raise WebHdfsError("missing Knox bearer")
    headers = {"Authorization": f"Bearer {token}"}
    if extra:
        headers.update(extra)
    return headers


def _request(
    method: str,
    path: str,
    token: str,
    *,
    params: dict[str, str] | None = None,
    content: bytes | None = None,
    extra_headers: dict[str, str] | None = None,
) -> httpx.Response:
    url = f"{gateway_url().rstrip('/')}{path}"
    verify = True
    env = load_env()
    if (env.get("UPSTREAM_TLS_VERIFY") or "false").lower() not in {"true", "1", "yes"}:
        verify = False
    try:
        return httpx.request(
            method.upper(),
            url,
            headers=_headers(token, extra_headers),
            params=params,
            content=content,
            timeout=REQUEST_TIMEOUT,
            follow_redirects=False,
            verify=verify,
        )
    except httpx.HTTPError as exc:
        raise WebHdfsError(str(exc)) from exc


def _json_or_error(response: httpx.Response, token: str) -> dict:
    text = (response.text or "").replace(token, "<redacted>")
    if response.status_code >= 400:
        raise WebHdfsError(f"WebHDFS {response.status_code}: {text[:300]}")
    if not response.content:
        return {}
    try:
        payload = response.json()
    except ValueError as exc:
        raise WebHdfsError(f"WebHDFS returned non-JSON: {text[:200]}") from exc
    if not isinstance(payload, dict):
        raise WebHdfsError("WebHDFS returned a non-object JSON body")
    return payload


def mkdir(hdfs_path: str, token: str) -> bool:
    response = _request("PUT", webhdfs_path(hdfs_path), token, params={"op": "MKDIRS"})
    payload = _json_or_error(response, token)
    return bool(payload.get("boolean", True))


def list_status(hdfs_path: str, token: str) -> dict:
    response = _request("GET", webhdfs_path(hdfs_path), token, params={"op": "LISTSTATUS"})
    return _json_or_error(response, token)


def file_status(hdfs_path: str, token: str) -> dict:
    response = _request("GET", webhdfs_path(hdfs_path), token, params={"op": "GETFILESTATUS"})
    return _json_or_error(response, token)


def put_file(local_path: Path, hdfs_path: str, token: str, *, env: dict[str, str] | None = None) -> dict:
    data = Path(local_path).read_bytes()
    dest = webhdfs_path(hdfs_path)[len(WEBHDFS_V1) :] or "/"
    parent = str(Path(dest).parent)
    if parent not in {"", "/"}:
        mkdir(parent, token)
    create = _request(
        "PUT",
        webhdfs_path(dest),
        token,
        params={"op": "CREATE", "overwrite": "true", "noredirect": "true"},
    )
    payload = _json_or_error(create, token)
    location = create.headers.get("Location") or create.headers.get("location") or payload.get("Location")
    if not location:
        raise WebHdfsError("CREATE did not return a Location")
    rewritten = gateway_create_location(str(location), env or load_env())
    parsed = urlparse(rewritten)
    query = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
    uploaded = _request(
        "PUT",
        parsed.path,
        token,
        params=query or None,
        content=data,
        extra_headers={"Content-Type": "application/octet-stream"},
    )
    if uploaded.status_code not in {200, 201}:
        _json_or_error(uploaded, token)
    return file_status(dest, token)


def format_listing(payload: dict) -> list[str]:
    statuses = (((payload.get("FileStatuses") or {}).get("FileStatus")) or [])
    lines: list[str] = []
    for item in statuses:
        if not isinstance(item, dict):
            continue
        kind = "d" if item.get("type") == "DIRECTORY" else "-"
        name = item.get("pathSuffix") or ""
        owner = item.get("owner") or ""
        lines.append(f"{kind} {owner} {name}".rstrip())
    return lines
