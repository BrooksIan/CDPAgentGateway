# CML run_session executes this as IPython cells. Do not use __file__, a module
# docstring, or __main__ — those fail in the notebook kernel.
import os
import shutil
import subprocess
import sys
from pathlib import Path
_root = Path(os.environ.get("AGENTGATEWAY_ROOT") or Path.cwd()).resolve()
if not (_root / "cml_path.py").is_file():
    _root = Path("/home/cdsw")
sys.path.insert(0, str(_root))
import cml_path
ROOT = cml_path.bootstrap()
from agentgateway.cml_boot import require_python
require_python()
subprocess.check_call([sys.executable, "-m", "pip", "install", "--user", "-e", f"{ROOT}[amp]"], cwd=str(ROOT))
try:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--user", "-e", f"{ROOT}[hive]"], cwd=str(ROOT))
except subprocess.CalledProcessError as exc:
    print(f"warning: hive extra failed (exit {exc.returncode}); Spark and admin still start", file=sys.stderr)
if shutil.which("docker"):
    subprocess.run(["docker", "pull", "apache/apisix:3.16.0-debian"], check=False)
