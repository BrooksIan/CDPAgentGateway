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
        print("error: KNOX_PROXY_URL is required", file=sys.stderr)
        return 2
    generated = ROOT / "conf" / "generated" / "knox-public.pem"
    live = ROOT / "conf" / "keys" / "knox-live.pem"
    out = fetch_pinned_knox_pubkey(
        knox_proxy_url=proxy,
        jwks_url=os.environ.get("KNOX_JWKS_URL"),
        out=generated,
        insecure=_insecure(),
    )
    live.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(out, live)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
