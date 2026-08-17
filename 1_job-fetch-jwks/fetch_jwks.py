#!/usr/bin/env python3
# CML may execute jobs in IPython. Do not use __file__ here.
import os
import shutil
import sys
from pathlib import Path
_root = Path(os.environ.get("AGENTGATEWAY_ROOT") or Path.cwd()).resolve()
if not (_root / "pyproject.toml").is_file():
    _root = Path("/home/cdsw").resolve()
sys.path.insert(0, str(_root / "src"))
from agentgateway.cml_boot import project_root
from agentgateway.keys import fetch_pinned_knox_pubkey
ROOT = project_root()

def _insecure() -> bool:
    return os.environ.get("UPSTREAM_TLS_VERIFY", "true").lower() in {"false", "0", "no"}

def main() -> int:
    proxy = (os.environ.get("KNOX_PROXY_URL") or "").strip()
    if not proxy:
        print("warning: KNOX_PROXY_URL unset; skipping JWKS pin so applications can still listen", file=sys.stderr)
        return 0
    generated = ROOT / "conf" / "generated" / "knox-public.pem"
    live = ROOT / "conf" / "keys" / "knox-live.pem"
    try:
        out = fetch_pinned_knox_pubkey(
            knox_proxy_url=proxy,
            jwks_url=os.environ.get("KNOX_JWKS_URL"),
            out=generated,
            insecure=_insecure(),
        )
    except Exception as exc:
        print(f"warning: JWKS pin failed: {type(exc).__name__}; applications will still start", file=sys.stderr)
        return 0
    live.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(out, live)
    print(out)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
