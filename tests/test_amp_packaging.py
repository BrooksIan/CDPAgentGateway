from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_amp_metadata_is_optional_and_not_launchable() -> None:
    meta = yaml.safe_load((ROOT / "METADATA.yaml").read_text())
    assert meta["launchable"] is False
    assert "Launchable (AMP)" not in meta["catalog_classification"]

    amp = yaml.safe_load((ROOT / ".project-metadata.yaml").read_text())
    assert amp["name"] == "CDP Agent Gateway"
    env = amp["environment_variables"]
    assert env["KNOX_PROXY_URL"]["required"] is True
    assert "KNOX_TOKEN" not in env
    scripts = [task.get("script") for task in amp["tasks"] if "script" in task]
    assert "0_session-install-dependencies/install_dependencies.py" in scripts
    assert "1_job-fetch-jwks/fetch_jwks.py" in scripts
    assert "2_job-smoke-knox/smoke_knox.py" in scripts
    assert "3_app-mcp-spark/app.py" in scripts
    assert "5_app-mcp-hive/app.py" in scripts
    assert "6_app-mcp-impala/app.py" in scripts
    assert "4_app-operator-admin/app.py" in scripts

    install = next(task for task in amp["tasks"] if task.get("script") == "0_session-install-dependencies/install_dependencies.py")
    assert install["type"] == "run_session"
    assert "cpu" in install and "memory" in install
    mcp = next(task for task in amp["tasks"] if task.get("subdomain") == "mcp-spark")
    hive = next(task for task in amp["tasks"] if task.get("subdomain") == "mcp-hive")
    impala = next(task for task in amp["tasks"] if task.get("subdomain") == "mcp-impala")
    admin = next(task for task in amp["tasks"] if task.get("subdomain") == "gateway-admin")
    assert mcp["type"] == "start_application"
    assert mcp["kernel"] == "python3"
    assert mcp["bypass_authentication"] is True
    assert hive["bypass_authentication"] is True
    assert impala["bypass_authentication"] is True
    assert admin["bypass_authentication"] is False
    first_app = next(i for i, task in enumerate(amp["tasks"]) if task["type"] == "start_application")
    first_knox = next(i for i, task in enumerate(amp["tasks"]) if task.get("entity_label") == "fetch_jwks")
    assert first_app < first_knox, "start_application must run before Knox jobs or a failed JWKS task skips the apps"

    install_src = (ROOT / "0_session-install-dependencies" / "install_dependencies.py").read_text()
    assert "--user" in install_src
    assert '[amp]' in install_src
    assert "if __name__" not in install_src
    assert "Path(__file__)" not in install_src


def test_cml_project_root_does_not_need_caller_file(tmp_path, monkeypatch) -> None:
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
        ROOT / "src" / "agentgateway" / "knox_jwt.py",
        ROOT / "src" / "agentgateway" / "amp.py",
        ROOT / "src" / "agentgateway" / "cml_boot.py",
    ):
        assert path.is_file(), path
