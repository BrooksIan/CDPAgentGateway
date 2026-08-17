from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_README_HEADINGS = [
    "## Overview",
    "## Demo",
    "## Use Case",
    "## Key Features",
    "## Quickstart",
    "## Architecture / Software Components",
    "## Target Audience",
    "## Repository Structure",
    "## Prerequisites",
    "## Hardware Requirements",
    "## Documentation",
]

REQUIRED_METADATA_KEYS = [
    "name",
    "slug",
    "description",
    "reprise_link",
    "catalog_classification",
    "launchable",
    "lifecycle_status",
    "industry",
    "industry_alignment",
    "tags",
    "product_mapping",
    "publisher",
    "source",
    "public_github_link",
    "maintainer_name",
    "date_created",
    "date_updated",
    "published_date",
]


def test_readme_has_blueprint_standard_sections() -> None:
    text = (ROOT / "README.md").read_text()
    assert text.startswith("# Cloudera Blueprint:")
    for heading in REQUIRED_README_HEADINGS:
        assert heading in text, f"README missing required section {heading}"
    assert "assets/" in text
    assert "deploy/" in text
    assert "METADATA.yaml" in text
    assert "docs/amp.md" in text
    assert "docs/spark.md" in text
    assert "docs/hive.md" in text
    assert "/mcp/spark" in text
    assert "127.0.0.1:9090" in text or "localhost `:9090`" in text


def test_metadata_yaml_has_catalog_fields() -> None:
    meta = yaml.safe_load((ROOT / "METADATA.yaml").read_text())
    for key in REQUIRED_METADATA_KEYS:
        assert key in meta, f"METADATA.yaml missing {key}"
    assert meta["name"]
    assert meta["slug"] == "cdp-agent-gateway"
    assert meta["catalog_classification"]
    assert "Enterprise Blueprint" in meta["catalog_classification"]
    assert meta["launchable"] is False
    assert meta["lifecycle_status"] == "Active"
    assert "Cloudera Data Platform" in meta["product_mapping"]
    assert "agents" in meta["tags"]
    assert "spark" in meta["tags"]
    assert "Cloudera Data Engineering" in meta["product_mapping"]


def test_blueprint_layout_dirs_exist() -> None:
    for path in (
        ROOT / "assets" / "architecture.svg",
        ROOT / "deploy" / "docker-compose.yml",
        ROOT / "docs" / "architecture.md",
        ROOT / "mcp-spark" / "server.py",
        ROOT / "admin" / "server.py",
        ROOT / "examples" / "spark" / "count_to_10.py",
        ROOT / "docs" / "operator-cli.md",
        ROOT / "docs" / "spark.md",
        ROOT / "docs" / "hive.md",
        ROOT / "docs" / "amp.md",
        ROOT / ".project-metadata.yaml",
        ROOT / "catalog-entry.yaml",
        ROOT / "METADATA.yaml",
    ):
        assert path.exists(), f"missing blueprint path {path.relative_to(ROOT)}"
