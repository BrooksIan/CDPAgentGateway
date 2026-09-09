#!/usr/bin/env python3
# CML may execute this as IPython. Do not use __file__ here.
import os
import sys
from pathlib import Path
_root = Path(os.environ.get("AGENTGATEWAY_ROOT") or Path.cwd()).resolve()
if not (_root / "src" / "agentgateway" / "cml_boot.py").is_file():
    _root = Path("/home/cdsw")
sys.path.insert(0, str(_root / "src"))
from agentgateway.cml_boot import mcp_adapter_app
app, service = mcp_adapter_app("hive")
from agentgateway.amp import serve_cml_app
if __name__ == "__main__" or os.environ.get("CDSW_APP_PORT"):
    serve_cml_app(app, service=service)
