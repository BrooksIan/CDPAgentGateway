from __future__ import annotations

import shutil
from pathlib import Path

from agentgateway.env import load_env, render_apisix_yaml
from agentgateway.keys import generate_test_keys
from agentgateway.paths import repo_root


def write_apisix_config() -> Path:
    root = repo_root()
    values = load_env()
    generate_test_keys()
    out_dir = root / "conf" / "generated"
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / "knox-public.pem"
    mode = values.get("GATEWAY_MODE", "local")
    if mode == "live":
        src = Path(values.get("KNOX_PUBLIC_KEY_FILE", root / "conf" / "keys" / "knox-live.pem"))
        if not src.is_absolute():
            src = root / src
        if not src.exists():
            raise FileNotFoundError(
                f"Live mode needs a Knox public key at {src}. Run: gateway fetch-jwks --jwks-url $KNOX_JWKS_URL"
            )
    else:
        src = root / "conf" / "keys" / "public.pem"
        if not src.exists():
            raise FileNotFoundError("Missing local test keys; run: gateway init")
    shutil.copyfile(src, dest)
    rendered = render_apisix_yaml((root / "conf" / "apisix.yaml.tpl").read_text(), values)
    out = out_dir / "apisix.yaml"
    out.write_text(rendered)
    if not out.read_text().rstrip().endswith("#END"):
        raise ValueError("apisix.yaml must end with #END")
    return out
