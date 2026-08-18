from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_amp_metadata_is_optional_and_not_launchable() -> None:
    meta = yaml.safe_load((ROOT / "METADATA.yaml").read_text())
    assert meta["launchable"] is False
    assert "Launchable (AMP)" not in meta["catalog_classification"]

    amp = yaml.safe_load((ROOT / ".project-metadata.yaml").read_text())
    assert amp["name"] == "CDP Agent Gateway"
    env = amp["environment_variables"]
    knox_url = env["KNOX_PROXY_URL"]
    assert knox_url["required"] is True
    default = knox_url.get("default") or ""
    assert default.startswith("https://")
    assert "cdp-proxy-token" in default
    assert "livy_for_spark3" in default
    assert "KNOX_TOKEN" not in env
    assert env["ENABLE_MCP_SPARK"]["default"] == "true"
    assert env["ENABLE_MCP_HIVE"]["default"] == "true"
    assert env["ENABLE_MCP_IMPALA"]["default"] == "false"
    assert env["ENABLE_MCP_SPARK"]["required"] is False
    assert env["ENABLE_MCP_HIVE"]["required"] is False
    assert env["ENABLE_MCP_IMPALA"]["required"] is False
    types = [task["type"] for task in amp["tasks"]]
    assert types.count("create_job") == 2
    assert types.count("run_job") == 2
    fetch_create = next(i for i, task in enumerate(amp["tasks"]) if task.get("type") == "create_job" and task.get("entity_label") == "fetch_jwks")
    fetch_run = next(i for i, task in enumerate(amp["tasks"]) if task.get("type") == "run_job" and task.get("entity_label") == "fetch_jwks")
    assert fetch_run == fetch_create + 1
    smoke_create = next(i for i, task in enumerate(amp["tasks"]) if task.get("type") == "create_job" and task.get("entity_label") == "smoke_knox")
    smoke_run = next(i for i, task in enumerate(amp["tasks"]) if task.get("type") == "run_job" and task.get("entity_label") == "smoke_knox")
    assert smoke_run == smoke_create + 1
    scripts = [task.get("script") for task in amp["tasks"] if "script" in task]
    assert "0_session-install-dependencies/install_dependencies.py" in scripts
    assert "1_job-fetch-jwks/fetch_jwks.py" in scripts
    assert "2_job-smoke-knox/smoke_knox.py" in scripts
    assert "3_app-mcp-spark/app.py" in scripts
    assert "5_app-mcp-hive/app.py" in scripts
    assert "6_app-mcp-impala/app.py" in scripts
    assert "7_app-agent-gateway/app.py" in scripts
    assert "4_app-operator-admin/app.py" in scripts

    kernels = {runtime["kernel"] for runtime in amp["runtimes"]}
    editors = {runtime["editor"] for runtime in amp["runtimes"]}
    assert "Python 3.11" in kernels
    assert "JupyterLab" in editors
    assert {
        "editor": "JupyterLab",
        "kernel": "Python 3.11",
        "edition": "Standard",
    } in amp["runtimes"]
    for runtime in amp["runtimes"]:
        major, minor = runtime["kernel"].removeprefix("Python ").split(".")
        assert (int(major), int(minor)) >= (3, 11)
    for task in amp["tasks"]:
        if task.get("kernel") != "python3":
            continue
        task_kernels = {runtime["kernel"] for runtime in task["runtimes"]}
        task_editors = {runtime["editor"] for runtime in task["runtimes"]}
        assert "Python 3.11" in task_kernels
        assert "Python 3.12" in task_kernels
        assert "JupyterLab" in task_editors
    install = next(task for task in amp["tasks"] if task.get("script") == "0_session-install-dependencies/install_dependencies.py")
    assert install["type"] == "run_session"
    assert install["entity_label"] == "install_deps"
    assert "cpu" in install and "memory" in install
    mcp = next(task for task in amp["tasks"] if task.get("subdomain") == "mcp-spark")
    hive = next(task for task in amp["tasks"] if task.get("subdomain") == "mcp-hive")
    impala = next(task for task in amp["tasks"] if task.get("subdomain") == "mcp-impala")
    apisix = next(task for task in amp["tasks"] if task.get("subdomain") == "agent-gateway")
    admin = next(task for task in amp["tasks"] if task.get("subdomain") == "gateway-admin")
    assert mcp["type"] == "start_application"
    assert mcp["kernel"] == "python3"
    assert mcp["bypass_authentication"] is True
    assert hive["bypass_authentication"] is True
    assert impala["bypass_authentication"] is True
    assert apisix["type"] == "start_application"
    assert apisix["bypass_authentication"] is True
    assert admin["bypass_authentication"] is False
    labels = [task.get("entity_label") for task in amp["tasks"] if task.get("entity_label")]
    assert labels.index("install_deps") < labels.index("fetch_jwks")
    assert labels.index("fetch_jwks") < labels.index("spark-mcp")
    assert labels.index("smoke_knox") < labels.index("spark-mcp")
    assert labels.index("spark-mcp") < labels.index("agent-gateway")

    install_src = (ROOT / "0_session-install-dependencies" / "install_dependencies.py").read_text()
    assert "--user" in install_src
    assert '[amp]' in install_src
    assert "if __name__" not in install_src
    assert "require_python" in install_src


def test_require_python_rejects_old_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentgateway import cml_boot

    monkeypatch.setattr(cml_boot.sys, "version_info", (3, 10, 14))
    monkeypatch.setattr(cml_boot.sys, "version", "3.10.14 (default)")
    with pytest.raises(RuntimeError, match="3.11"):
        cml_boot.require_python()


def test_run_amp_main_does_not_systemexit_in_ipython(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentgateway import cml_boot

    monkeypatch.setenv("CDSW_ENGINE_ID", "test-engine")
    assert cml_boot.in_ipython() is True
    assert cml_boot.run_amp_main(lambda: 0) == 0


def test_knox_jobs_do_not_raise_systemexit() -> None:
    fetch = (ROOT / "1_job-fetch-jwks" / "fetch_jwks.py").read_text()
    smoke = (ROOT / "2_job-smoke-knox" / "smoke_knox.py").read_text()
    assert "SystemExit" not in fetch
    assert "SystemExit" not in smoke
    assert "missing verifying PEM" not in smoke
    assert "run_amp_main(main)" in fetch
    assert "run_amp_main(main)" in smoke


def test_cml_project_root_does_not_need_caller_file(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentgateway.cml_boot import project_root

    monkeypatch.chdir(ROOT)
    monkeypatch.delenv("AGENTGATEWAY_ROOT", raising=False)
    assert project_root() == ROOT


def test_install_script_runs_in_ipython_without_file(monkeypatch) -> None:
    import subprocess

    monkeypatch.chdir(ROOT)
    monkeypatch.delenv("AGENTGATEWAY_ROOT", raising=False)
    monkeypatch.setattr(subprocess, "check_call", lambda *args, **kwargs: None)
    script = (ROOT / "0_session-install-dependencies" / "install_dependencies.py").read_text()
    namespace: dict[str, object] = {"__name__": "__main__"}
    exec(compile(script, "<ipython>", "exec"), namespace)
    assert namespace["ROOT"] == ROOT


def test_amp_layout_and_catalog_exist() -> None:
    catalog = yaml.safe_load((ROOT / "catalog-entry.yaml").read_text())
    assert catalog["entries"][0]["label"] == "cdp-agent-gateway"
    git_url = catalog["entries"][0]["git_url"]
    assert git_url.startswith("https://github.com/"), git_url
    assert git_url.endswith(".git"), git_url
    cover = ROOT / catalog["entries"][0]["image_path"]
    assert cover.is_file(), cover
    assert cover.suffix.lower() in {".jpg", ".jpeg", ".png"}
    for path in (
        ROOT / "docs" / "amp.md",
        ROOT / "0_session-install-dependencies" / "install_dependencies.py",
        ROOT / "1_job-fetch-jwks" / "fetch_jwks.py",
        ROOT / "2_job-smoke-knox" / "smoke_knox.py",
        ROOT / "3_app-mcp-spark" / "app.py",
        ROOT / "4_app-operator-admin" / "app.py",
        ROOT / "5_app-mcp-hive" / "app.py",
        ROOT / "6_app-mcp-impala" / "app.py",
        ROOT / "7_app-agent-gateway" / "app.py",
        ROOT / "src" / "agentgateway" / "knox_jwt.py",
        ROOT / "src" / "agentgateway" / "amp.py",
        ROOT / "src" / "agentgateway" / "amp_apisix.py",
        ROOT / "src" / "agentgateway" / "cml_boot.py",
    ):
        assert path.is_file(), path
