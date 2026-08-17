from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import jwt

ROOT = Path(__file__).resolve().parents[1]


def run_gateway(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    src = str(ROOT / "src")
    env["PYTHONPATH"] = src + ((":" + env["PYTHONPATH"]) if env.get("PYTHONPATH") else "")
    return subprocess.run(
        [sys.executable, "-m", "agentgateway", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=check,
        env=env,
    )


def test_help_lists_operator_commands() -> None:
    result = run_gateway("--help")
    assert result.returncode == 0
    for command in ("init", "up", "down", "test", "token", "call", "spark", "webhdfs", "hive", "mcp", "admin", "doctor", "knox", "jdbc", "fetch-jwks"):
        assert command in result.stdout


def test_token_mints_knox_shaped_rs256() -> None:
    run_gateway("init", check=True)
    result = run_gateway("token", "mint", "--sub", "cli-user")
    token = result.stdout.strip()
    header = jwt.get_unverified_header(token)
    payload = jwt.decode(token, options={"verify_signature": False})
    assert header["alg"] == "RS256"
    assert payload["iss"] == "KNOXSSO"
    assert payload["sub"] == "cli-user"
    assert payload["knox.id"]


def test_token_help_lists_set_show_clear() -> None:
    result = run_gateway("token", "--help")
    assert result.returncode == 0
    for command in ("set", "show", "clear", "mint"):
        assert command in result.stdout


def test_jdbc_help_lists_add_show_clear() -> None:
    result = run_gateway("jdbc", "--help")
    assert result.returncode == 0
    for command in ("add", "show", "clear"):
        assert command in result.stdout


def test_webhdfs_help_lists_ls_stat_mkdir_put() -> None:
    result = run_gateway("webhdfs", "--help")
    assert result.returncode == 0
    for command in ("ls", "stat", "mkdir", "put"):
        assert command in result.stdout


def test_doctor_passes_after_init() -> None:
    run_gateway("init", check=True)
    result = run_gateway("doctor", check=False)
    assert "python" in result.stdout


def test_config_writes_apisix_yaml() -> None:
    run_gateway("init", check=True)
    generated = ROOT / "conf" / "generated" / "apisix.yaml"
    assert generated.exists()
    text = generated.read_text()
    assert text.rstrip().endswith("#END")
    assert "knox-jwt" in text
    assert "livy_for_spark3" in text
    assert "mcp-spark" in text
    assert "mcp-hive" in text
    assert "uri: /mcp/hive*" in text
    assert "uri: /cdp/hive" not in text
    assert 'uri: /cdp/livy_for_spark3*' in text
    assert 'uri: /cdp/webhdfs*' in text
    assert 'methods: ["GET", "HEAD"]' in text
    assert 'methods: ["GET", "HEAD", "PUT"]' in text
    assert 'methods: ["GET", "HEAD", "POST", "PUT", "DELETE"]' not in text


def test_tool_arguments_split_livy_args_list() -> None:
    from agentgateway.probe import tool_arguments

    parsed = tool_arguments(
        [
            "file=hdfs:///user/analyst/job.py",
            "name=count-to-10",
            "args=analyst,count_to_10",
            "batch_id=3",
        ]
    )
    assert parsed["args"] == ["analyst", "count_to_10"]
    assert parsed["batch_id"] == 3
    assert parsed["file"].startswith("hdfs://")
