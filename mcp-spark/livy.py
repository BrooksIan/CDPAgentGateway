"""Livy HTTP client. Forwards the caller's Knox bearer; never logs it."""

from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.parse import urlparse

import httpx

SPARK_LIVY_SERVICE = "livy_for_spark3"
MAX_LIST_ITEMS = 25
MAX_LOG_LINES = 80
MAX_LOG_CHARS = 8000
MAX_BATCH_ID = 1_000_000
REQUEST_TIMEOUT = 30.0
MAX_ARGS = 20
MAX_ARG_CHARS = 2048
MAX_CONF_KEYS = 20
ALLOWED_FILE_SCHEMES = {"hdfs", "s3a", "s3", "abfs", "abfss", "o3fs", "ofs", "viewfs"}
DENIED_CONF_MARKERS = (
    "password",
    "secret",
    "keytab",
    "principal",
    "credential",
    "proxyuser",
    "javax.jdo",
)
_SECRET_MESSAGE_MARKERS = ("bearer ", "authorization", "password", "token=")
MAX_LIVY_MESSAGE = 400

_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-+=/]+")
_BATCH_ID = re.compile(r"^[0-9]+$")


def redact_secrets(text: str) -> str:
    """Strip JWT-shaped strings and Bearer values before logs reach a model."""
    cleaned = _JWT_RE.sub("[redacted]", text)
    return _BEARER_RE.sub("Bearer [redacted]", cleaned)


class LivyError(Exception):
    def __init__(self, message: str, *, status: int = 502, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.details = details or {}


def livy_base_url(
    *,
    scheme: str | None = None,
    host: str | None = None,
    port: str | None = None,
    prefix: str | None = None,
) -> str:
    scheme = (scheme or os.environ.get("UPSTREAM_SCHEME", "http")).rstrip(":/")
    host = host or os.environ.get("UPSTREAM_HOST", "mock-cdp")
    port = port or os.environ.get("UPSTREAM_PORT", "8080")
    prefix = (prefix or os.environ.get("KNOX_PROXY_PREFIX", "/gateway/cdp-proxy-token")).rstrip("/")
    origin = f"{scheme}://{host}"
    if not ((scheme == "https" and str(port) == "443") or (scheme == "http" and str(port) == "80")):
        origin = f"{origin}:{port}"
    return f"{origin}{prefix}/{SPARK_LIVY_SERVICE}"


def tls_verify() -> bool:
    return os.environ.get("UPSTREAM_TLS_VERIFY", "false").lower() in {"true", "1", "yes"}


def livy_relpath(kind: str, batch_id: int | None = None) -> str:
    if kind == "sessions":
        return "/sessions"
    if kind == "batches":
        return "/batches"
    if kind in {"batch", "log"}:
        if batch_id is None or batch_id < 0 or batch_id >= MAX_BATCH_ID:
            raise LivyError("batch_id must be a non-negative integer", status=400)
        if kind == "batch":
            return f"/batches/{batch_id}"
        return f"/batches/{batch_id}/log"
    raise LivyError(f"unknown livy kind {kind!r}", status=400)


def public_batch(item: dict[str, Any]) -> dict[str, Any]:
    keep = ("id", "name", "state", "appId", "appInfo", "owner", "proxyUser")
    return {key: item[key] for key in keep if key in item}


def truncate_log(payload: dict[str, Any]) -> dict[str, Any]:
    lines = payload.get("log")
    if not isinstance(lines, list):
        text = str(payload.get("log") or "")
        lines = text.splitlines()
    clipped = [redact_secrets(str(line)) for line in lines[-MAX_LOG_LINES:]]
    joined = "\n".join(clipped)
    truncated = False
    if len(joined) > MAX_LOG_CHARS:
        joined = joined[-MAX_LOG_CHARS:]
        truncated = True
    if len(lines) > MAX_LOG_LINES:
        truncated = True
    return {
        "id": payload.get("id"),
        "from": payload.get("from", 0),
        "size": len(clipped),
        "truncated": truncated,
        "log": joined.split("\n") if joined else [],
    }


def normalize_list(payload: dict[str, Any], *, kind: str) -> dict[str, Any]:
    items = payload.get("sessions") or payload.get("batches") or payload.get("jobs") or []
    if not isinstance(items, list):
        items = []
    sliced = [public_batch(item) if isinstance(item, dict) else {"value": item} for item in items[:MAX_LIST_ITEMS]]
    return {
        "kind": kind,
        "from": payload.get("from", 0),
        "total": payload.get("total", len(items)),
        "returned": len(sliced),
        "truncated": len(items) > MAX_LIST_ITEMS,
        "items": sliced,
    }


def get_json(
    kind: str,
    *,
    authorization: str,
    batch_id: int | None = None,
    params: dict[str, Any] | None = None,
    request_id: str | None = None,
    knox_user: str | None = None,
) -> dict[str, Any]:
    rel = livy_relpath(kind, batch_id)
    url = f"{livy_base_url().rstrip('/')}{rel}"
    headers = {"Authorization": authorization, "Accept": "application/json"}
    if request_id:
        headers["X-Request-Id"] = request_id
    try:
        response = httpx.get(
            url,
            headers=headers,
            params=params,
            timeout=REQUEST_TIMEOUT,
            verify=tls_verify(),
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        raise LivyError(f"livy unreachable: {exc.__class__.__name__}", status=502) from exc

    body = _decode_body(response)
    if response.status_code >= 400:
        raise LivyError(
            "livy request failed",
            status=response.status_code,
            details=_livy_failure_details(response.status_code, rel, body),
        )

    if kind == "sessions":
        shaped = normalize_list(body, kind="sessions")
    elif kind == "batches":
        shaped = normalize_list(body, kind="batches")
    elif kind == "log":
        shaped = truncate_log(body)
    else:
        shaped = public_batch(body) if isinstance(body, dict) else {"value": body}

    shaped["knox_user"] = knox_user
    shaped["request_id"] = request_id
    return shaped


def public_livy_message(body: dict[str, Any], *, limit: int = MAX_LIVY_MESSAGE) -> str | None:
    candidates: list[str] = []
    for key in ("msg", "message", "error", "exception"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            candidates.append(value.strip())
    nested = body.get("value")
    if isinstance(nested, str) and nested.strip():
        candidates.append(nested.strip())
    for text in candidates:
        lowered = text.lower()
        if any(marker in lowered for marker in _SECRET_MESSAGE_MARKERS):
            continue
        return text[:limit]
    return None


def _livy_failure_details(status: int, rel: str, body: dict[str, Any]) -> dict[str, Any]:
    details: dict[str, Any] = {"livy_status": status, "livy_path": rel}
    message = public_livy_message(body)
    if message:
        details["livy_message"] = message
    return details


def _decode_body(response: httpx.Response) -> dict[str, Any]:
    ctype = (response.headers.get("content-type") or "").lower()
    if "json" in ctype:
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise LivyError("livy returned invalid JSON", status=502) from exc
        return data if isinstance(data, dict) else {"value": data}
    status = response.status_code if response.status_code >= 400 else 502
    text = (response.text or "").strip()
    details: dict[str, Any] = {"livy_status": response.status_code}
    message = public_livy_message({"message": text}) if text else None
    if message:
        details["livy_message"] = message
    raise LivyError(
        "livy returned a non-JSON body",
        status=status,
        details=details,
    )


def parse_batch_id(raw: Any) -> int:
    if isinstance(raw, bool) or raw is None:
        raise LivyError("batch_id is required", status=400)
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str) and _BATCH_ID.match(raw):
        value = int(raw)
    else:
        raise LivyError("batch_id must be a non-negative integer", status=400)
    if value < 0 or value >= MAX_BATCH_ID:
        raise LivyError("batch_id out of range", status=400)
    return value


def allow_file_scheme() -> bool:
    return os.environ.get("SPARK_ALLOW_FILE_SCHEME", "false").lower() in {"true", "1", "yes"}


def validate_resource_uri(uri: str, *, field: str = "file") -> str:
    raw = (uri or "").strip()
    if not raw or len(raw) > 2048:
        raise LivyError(f"{field} URI is required", status=400)
    if ".." in raw.split("://", 1)[-1]:
        raise LivyError(f"{field} URI must not contain ..", status=400)
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    allowed = set(ALLOWED_FILE_SCHEMES)
    if allow_file_scheme():
        allowed.add("file")
    if scheme not in allowed:
        raise LivyError(
            f"{field} must be an HDFS or object-store URI ({', '.join(sorted(allowed))})",
            status=400,
        )
    if not parsed.path or parsed.path == "/":
        raise LivyError(f"{field} URI is missing a path", status=400)
    return raw


def _string_list(raw: Any, *, field: str) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        items = [part.strip() for part in raw.split(",") if part.strip()]
    elif isinstance(raw, list):
        items = [str(part).strip() for part in raw if str(part).strip()]
    else:
        raise LivyError(f"{field} must be a list of strings", status=400)
    if len(items) > MAX_ARGS:
        raise LivyError(f"{field} is limited to {MAX_ARGS} entries", status=400)
    cleaned: list[str] = []
    for item in items:
        if len(item) > MAX_ARG_CHARS:
            raise LivyError(f"{field} entry is too long", status=400)
        cleaned.append(item)
    return cleaned


def _safe_conf(raw: Any) -> dict[str, str]:
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise LivyError("conf must be an object", status=400)
    if len(raw) > MAX_CONF_KEYS:
        raise LivyError(f"conf is limited to {MAX_CONF_KEYS} keys", status=400)
    conf: dict[str, str] = {}
    for key, value in raw.items():
        name = str(key).strip()
        lowered = name.lower()
        if any(marker in lowered for marker in DENIED_CONF_MARKERS):
            raise LivyError(f"conf key {name!r} is not allowed", status=400)
        if lowered.startswith("livy.") or lowered.startswith("javax."):
            raise LivyError(f"conf key {name!r} is not allowed", status=400)
        conf[name] = str(value)[:2048]
    return conf


def build_submit_body(arguments: dict[str, Any]) -> dict[str, Any]:
    if arguments.get("proxyUser") or arguments.get("proxy_user"):
        raise LivyError("proxyUser is not allowed; Ranger uses the Knox token subject", status=400)
    if arguments.get("code"):
        raise LivyError("inline Spark code is not allowed; submit a file URI", status=400)
    body: dict[str, Any] = {"file": validate_resource_uri(str(arguments.get("file") or ""), field="file")}
    name = str(arguments.get("name") or "").strip()
    if name:
        body["name"] = name[:200]
    class_name = str(arguments.get("className") or arguments.get("class_name") or "").strip()
    if class_name:
        body["className"] = class_name[:500]
    args = _string_list(arguments.get("args"), field="args")
    if args:
        body["args"] = args
    for field in ("jars", "pyFiles", "files", "archives"):
        uris = _string_list(arguments.get(field), field=field)
        if uris:
            body[field] = [validate_resource_uri(item, field=field) for item in uris]
    conf = _safe_conf(arguments.get("conf"))
    if conf:
        body["conf"] = conf
    return body


def post_json(
    kind: str,
    *,
    authorization: str,
    payload: dict[str, Any],
    request_id: str | None = None,
    knox_user: str | None = None,
) -> dict[str, Any]:
    if kind != "batches":
        raise LivyError("POST is only allowed for /batches", status=400)
    rel = livy_relpath("batches")
    url = f"{livy_base_url().rstrip('/')}{rel}"
    headers = {
        "Authorization": authorization,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if request_id:
        headers["X-Request-Id"] = request_id
    try:
        response = httpx.post(
            url,
            headers=headers,
            json=payload,
            timeout=REQUEST_TIMEOUT,
            verify=tls_verify(),
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        raise LivyError(f"livy unreachable: {exc.__class__.__name__}", status=502) from exc

    body = _decode_body(response)
    if response.status_code >= 400:
        raise LivyError(
            "livy submit failed",
            status=response.status_code,
            details=_livy_failure_details(response.status_code, rel, body),
        )
    shaped = public_batch(body) if isinstance(body, dict) else {"value": body}
    shaped["knox_user"] = knox_user
    shaped["request_id"] = request_id
    shaped["submitted"] = True
    return shaped
