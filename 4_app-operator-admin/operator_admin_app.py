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
# Not an MCP adapter: no enable-check, and this app runs bypass_authentication: false
# (CML-login-gated, not a public agent route).
app = cml_path.run_admin(name=__name__)
