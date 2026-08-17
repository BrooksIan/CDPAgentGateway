#!/usr/bin/env python3
"""AMP job: confirm pinned JWKS PEM exists and Knox is reachable. Never print tokens."""

from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentgateway.knox import parse_knox_proxy_url, trusted_jku  # noqa: E402
from agentgateway.paths import repo_root  # noqa: E402


def _insecure() -> bool:
    return os.environ.get("UPSTREAM_TLS_VERIFY", "true").lower() in {"false", "0", "no"}


def _open(url: str, *, headers: dict[str, str] | None = None):
    context = ssl._create_unverified_context() if _insecure() else ssl.create_default_context()
    request = urllib.request.Request(url, headers=headers or {}, method="GET")
    return urllib.request.urlopen(request, context=context, timeout=20)


def main() -> int:
    root = repo_root()
    pem = Path(os.environ.get("KNOX_PUBLIC_KEY_FILE") or root / "conf" / "generated" / "knox-public.pem")
    if not pem.is_file() or not pem.read_text().strip():
        print(f"error: missing verifying PEM at {pem}; run the fetch-jwks job", file=sys.stderr)
        return 2
    proxy = (os.environ.get("KNOX_PROXY_URL") or "").strip()
    if not proxy:
        print("error: KNOX_PROXY_URL is required", file=sys.stderr)
        return 2
    parsed = parse_knox_proxy_url(proxy)
    jwks = (os.environ.get("KNOX_JWKS_URL") or parsed["KNOX_JWKS_URL"]).strip()
    trusted_jku(jwks, parsed["UPSTREAM_HOST"])
    try:
        with _open(jwks) as response:
            jwks_status = response.status
    except (urllib.error.URLError, TimeoutError, ssl.SSLError) as exc:
        print(f"error: JWKS unreachable: {exc}", file=sys.stderr)
        return 1

    token = (os.environ.get("KNOX_TOKEN") or "").strip()
    livy_status = None
    if token:
        sessions = parsed["KNOX_PROXY_URL"].rstrip("/")
        if not sessions.endswith("/livy_for_spark3"):
            sessions = f"{parsed['KNOX_PROXY_URL'].rstrip('/')}/livy_for_spark3"
        try:
            with _open(f"{sessions}/sessions", headers={"Authorization": f"Bearer {token}"}) as response:
                livy_status = response.status
        except urllib.error.HTTPError as exc:
            livy_status = exc.code
        except (urllib.error.URLError, TimeoutError, ssl.SSLError) as exc:
            print(f"error: Livy probe failed: {exc}", file=sys.stderr)
            return 1

    print(
        json.dumps(
            {
                "pem": str(pem),
                "jwks_status": jwks_status,
                "token_probe": "ok" if livy_status is not None else "skipped",
                "livy_status": livy_status,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
