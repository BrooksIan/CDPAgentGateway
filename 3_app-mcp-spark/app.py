#!/usr/bin/env python3
"""CML Application: Spark MCP with in-process Knox JWT (not APISIX)."""

from __future__ import annotations

from agentgateway.amp import build_mcp_app, serve_cml_app

app = build_mcp_app()

if __name__ == "__main__":
    serve_cml_app(app, service="mcp-spark")
