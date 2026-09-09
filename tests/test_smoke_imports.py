from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent


def load_module(module_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    assert spec and spec.loader, f'cannot load spec for {module_name}'
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def test_python_sources_are_importable() -> None:
    sys.path.insert(0, str(PLUGIN_DIR))
    try:
        expectations = [{'path': 'provider/composio.py', 'module_name': 'composio_provider', 'class_name': 'ComposioProvider'}, {'path': 'tools/connect_toolkit.py', 'module_name': 'tools_connect_toolkit', 'class_name': 'ConnectToolkitTool'}, {'path': 'tools/create_session.py', 'module_name': 'tools_create_session', 'class_name': 'CreateSessionTool'}, {'path': 'tools/execute_batch.py', 'module_name': 'tools_execute_batch', 'class_name': 'ExecuteBatchTool'}, {'path': 'tools/execute_meta_tool.py', 'module_name': 'tools_execute_meta_tool', 'class_name': 'ExecuteMetaToolTool'}, {'path': 'tools/execute_tool.py', 'module_name': 'tools_execute_tool', 'class_name': 'ExecuteToolTool'}, {'path': 'tools/get_tool_schemas.py', 'module_name': 'tools_get_tool_schemas', 'class_name': 'GetToolSchemasTool'}, {'path': 'tools/inspect_session.py', 'module_name': 'tools_inspect_session', 'class_name': 'InspectSessionTool'}, {'path': 'tools/search_tools.py', 'module_name': 'tools_search_tools', 'class_name': 'SearchToolsTool'}]
        for expectation in expectations:
            module = load_module(expectation['module_name'], PLUGIN_DIR / expectation['path'])
            assert hasattr(module, expectation['class_name'])
    finally:
        sys.path.remove(str(PLUGIN_DIR))
