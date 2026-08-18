#!/usr/bin/env python3
# CML may execute this as IPython. Do not use __file__ here.
import os
import sys
from pathlib import Path
_root = Path(os.environ.get("AGENTGATEWAY_ROOT") or Path.cwd()).resolve()
if not (_root / ".project-metadata.yaml").is_file():
    _alt = Path("/home/cdsw")
    if (_alt / ".project-metadata.yaml").is_file():
        _root = _alt.resolve()
sys.path.insert(0, str(_root / "src"))
from agentgateway.cml_boot import ensure_amp_extra, ensure_src_path, project_root
_ROOT = project_root()
ensure_src_path(_ROOT)
ensure_amp_extra(_ROOT, extra="amp,hive")
from agentgateway.env import mcp_adapter_enabled
from agentgateway.amp import build_hive_mcp_app, disabled_mcp_app, serve_cml_app, startup_error_app
if not mcp_adapter_enabled("hive"):
    app = disabled_mcp_app("mcp-hive")
else:
    try:
        app = build_hive_mcp_app()
    except Exception as err:
        app = startup_error_app("mcp-hive", err)
if __name__ == "__main__" or os.environ.get("CDSW_APP_PORT"):
    serve_cml_app(app, service="mcp-hive")
