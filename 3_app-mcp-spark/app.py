#!/usr/bin/env python3
"""CML Application: Spark MCP with in-process Knox JWT (not APISIX)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from agentgateway.cml_boot import ensure_amp_extra, ensure_src_path  # noqa: E402

ensure_src_path(_ROOT)
ensure_amp_extra(_ROOT)

from agentgateway.amp import build_mcp_app, serve_cml_app, startup_error_app  # noqa: E402

try:
    app = build_mcp_app()
except Exception as exc:
    app = startup_error_app("mcp-spark", exc)

if __name__ == "__main__" or os.environ.get("CDSW_APP_PORT"):
    serve_cml_app(app, service="mcp-spark")
