#!/usr/bin/env python3
"""Fetch a Knox JWKS document and write an RSA public key PEM for APISIX."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentgateway.keys import fetch_knox_pubkey
from agentgateway.paths import repo_root


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jwks-url", required=True)
    parser.add_argument("--out", default=str(repo_root() / "conf" / "keys" / "knox-live.pem"))
    parser.add_argument("--insecure", action="store_true")
    args = parser.parse_args()
    out = fetch_knox_pubkey(args.jwks_url, Path(args.out), insecure=args.insecure)
    print(out)


if __name__ == "__main__":
    main()
