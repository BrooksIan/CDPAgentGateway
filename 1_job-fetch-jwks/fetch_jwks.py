#!/usr/bin/env python3
"""AMP job: fetch Knox JWKS from the pinned host and write a verifying PEM."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentgateway.keys import fetch_pinned_knox_pubkey  # noqa: E402


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
