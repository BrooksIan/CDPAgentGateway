#!/usr/bin/env python3
# CML may execute this as IPython. Do not use __file__ here.
import os
import sys
from pathlib import Path
_root = Path(os.environ.get("AGENTGATEWAY_ROOT") or Path.cwd()).resolve()
if not (_root / "src" / "agentgateway" / "cml_boot.py").is_file():
    _root = Path("/home/cdsw")
sys.path.insert(0, str(_root / "src"))
from agentgateway.cml_boot import boot_amp
boot_amp()
# Not an MCP adapter: no enable-check, and this app runs bypass_authentication: false
# (CML-login-gated, not a public agent route). Keep that distinction visible here.
from agentgateway.amp import build_admin_app, serve_cml_app, startup_error_app
try:
    app = build_admin_app()
except Exception as exc:
    app = startup_error_app("admin", exc)
if __name__ == "__main__" or os.environ.get("CDSW_APP_PORT"):
    serve_cml_app(app, service="admin")
