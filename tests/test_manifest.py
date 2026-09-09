from __future__ import annotations

from pathlib import Path

import yaml

PLUGIN_DIR = Path(__file__).resolve().parent.parent


def test_manifest_has_required_fields() -> None:
    manifest = yaml.safe_load((PLUGIN_DIR / "manifest.yaml").read_text(encoding="utf-8"))
    for key in [
        "author", "name", "type", "label", "description", "icon", "plugins", "resource", "version", "created_at"
    ]:
        assert key in manifest, f"manifest missing: {key}"
    runner = manifest.get("meta", {}).get("runner", {})
    assert runner.get("language") == "python"
    assert str(runner.get("version")).startswith("3.")
    assert runner.get("entrypoint") == "main"
    assert "tools" in manifest["plugins"]


def test_generated_yaml_files_exist() -> None:
    expected = ['provider/composio.yaml', 'tools/connect_toolkit.yaml', 'tools/create_session.yaml', 'tools/execute_batch.yaml', 'tools/execute_meta_tool.yaml', 'tools/execute_tool.yaml', 'tools/get_tool_schemas.yaml', 'tools/inspect_session.yaml', 'tools/search_tools.yaml']
    for rel in expected:
        assert (PLUGIN_DIR / rel).exists(), f"missing YAML file: {rel}"
