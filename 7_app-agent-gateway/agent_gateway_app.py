#!/usr/bin/env python3
# CML may execute this as IPython. Do not use __file__ here.
import os
import sys
from pathlib import Path
_root = Path(os.environ.get("AGENTGATEWAY_ROOT") or Path.cwd()).resolve()
if not (_root / "src" / "agentgateway" / "cml_boot.py").is_file():
    _root = Path("/home/cdsw")
sys.path.insert(0, str(_root / "src"))
from agentgateway.cml_boot import boot_amp, run_amp_main
boot_amp()
# No ASGI app here: serve_amp_apisix shells out to Docker APISIX, or falls back to the
# in-process Python edge. There is no (app, service) pair to return.
from agentgateway.amp_apisix import serve_amp_apisix
if __name__ == "__main__" or os.environ.get("CDSW_APP_PORT"):
    run_amp_main(serve_amp_apisix)
