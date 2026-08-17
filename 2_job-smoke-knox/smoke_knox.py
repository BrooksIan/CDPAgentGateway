#!/usr/bin/env python3
# CML may execute jobs in IPython. Do not use __file__ here.
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path
_root = Path(os.environ.get("AGENTGATEWAY_ROOT") or Path.cwd()).resolve()
if not (_root / "pyproject.toml").is_file():
    _root = Path("/home/cdsw").resolve()
sys.path.insert(0, str(_root / "src"))
from agentgateway.cml_boot import project_root, run_amp_main
from agentgateway.knox import parse_knox_proxy_url, trusted_jku
ROOT = project_root()

def _insecure() -> bool:
    return os.environ.get("UPSTREAM_TLS_VERIFY", "true").lower() in {"false", "0", "no"}

def _open(url: str, *, headers: dict[str, str] | None = None):
    context = ssl._create_unverified_context() if _insecure() else ssl.create_default_context()
    request = urllib.request.Request(url, headers=headers or {}, method="GET")
    return urllib.request.urlopen(request, context=context, timeout=20)

def main() -> int:
    pem = Path(os.environ.get("KNOX_PUBLIC_KEY_FILE") or ROOT / "conf" / "generated" / "knox-public.pem")
    if not pem.is_file() or not pem.read_text().strip():
        print('{"event":"knox_smoke_skipped","reason":"missing_pem"}')
        return 0
    proxy = (os.environ.get("KNOX_PROXY_URL") or "").strip()
    if not proxy:
        print('{"event":"knox_smoke_skipped","reason":"KNOX_PROXY_URL_unset"}')
        return 0
    try:
        parsed = parse_knox_proxy_url(proxy)
        jwks = (os.environ.get("KNOX_JWKS_URL") or parsed["KNOX_JWKS_URL"]).strip()
        trusted_jku(jwks, parsed["UPSTREAM_HOST"])
    except Exception as exc:
        print(f'{{"event":"knox_smoke_skipped","reason":"{type(exc).__name__}"}}')
        return 0
    try:
        with _open(jwks) as response:
            jwks_status = response.status
    except (urllib.error.URLError, TimeoutError, ssl.SSLError) as exc:
        print(f'{{"event":"knox_smoke_skipped","reason":"{type(exc).__name__}"}}')
        return 0
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
            print(f'{{"event":"livy_probe_skipped","reason":"{type(exc).__name__}"}}')
            livy_status = None
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

run_amp_main(main)
