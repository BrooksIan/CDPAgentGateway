#!/usr/bin/env python3
"""CML Application: operator admin UI. Keep CML login; not an agent MCP route."""

from __future__ import annotations

from agentgateway.amp import build_admin_app, serve_cml_app

app = build_admin_app()

if __name__ == "__main__":
    serve_cml_app(app, service="admin")
