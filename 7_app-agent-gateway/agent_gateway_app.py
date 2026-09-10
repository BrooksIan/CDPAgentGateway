#!/usr/bin/env python3
# CML may execute this as IPython. Do not use __file__ here.
import os
import sys
from pathlib import Path
_root = Path(os.environ.get("AGENTGATEWAY_ROOT") or Path.cwd()).resolve()
if not (_root / "cml_path.py").is_file():
    _root = Path("/home/cdsw")
sys.path.insert(0, str(_root))
import cml_path
# No ASGI app here: serve_amp_apisix shells out to Docker APISIX, or falls back to the
# in-process Python edge.
cml_path.run_agent_gateway(name=__name__)
