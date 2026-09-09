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
from agentgateway.cml_boot import ensure_amp_extra, ensure_src_path, project_root, run_amp_main

_ROOT = project_root()
ensure_src_path(_ROOT)
ensure_amp_extra(_ROOT)
from agentgateway.amp_apisix import serve_amp_apisix

if __name__ == "__main__" or os.environ.get("CDSW_APP_PORT"):
    run_amp_main(serve_amp_apisix)
