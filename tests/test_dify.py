import importlib
from types import SimpleNamespace

import pytest
import yaml
from dify_plugin.core.plugin_registration import PluginRegistration
from dify_plugin.entities.tool import ToolRuntime
from dify_plugin.errors.tool import ToolProviderCredentialValidationError

from common.client import ComposioClient, ComposioError
from common.service import ComposioService
from provider.composio import ComposioProvider
from tools.search_tools import SearchToolsTool


def test_real_sdk_registration_and_all_tool_schemas(plugin_directory, plugin_environment):
    registration = PluginRegistration(plugin_environment)
    assert "composio" in registration.tools_mapping
    provider = registration.tools_configuration[0]
    assert len(provider.tools) == 9
    for config in provider.tools:
        source = yaml.safe_load((plugin_directory / config.extra.python.source.replace(".py", ".yaml")).read_text())
        assert source["output_schema"]["properties"]["success"]["type"] == "boolean"
        user_parameter = next(parameter for parameter in config.parameters if parameter.name == "user_id")
        assert user_parameter.form.value == "form"
        module = importlib.import_module(config.extra.python.source.replace("/", ".").removesuffix(".py"))
        assert module


def test_native_tool_messages_and_variables(monkeypatch):
    payload = {"success": True, "session_id": "trs_test", "result": {"data": {"items": [1]}}}
    monkeypatch.setattr(ComposioService, "run", lambda *arguments: payload)
    tool = SearchToolsTool(
        runtime=ToolRuntime(credentials={"api_key": "test-key"}, user_id=None, session_id=None),
        session=SimpleNamespace(storage=object()),
    )
    messages = list(tool._invoke({"user_id": "user-123", "session_id": "trs_test", "query": "read mail"}))
    assert [message.type.value for message in messages] == ["json", "text", "variable", "variable"]
    assert messages[0].message.json_object == payload
    assert messages[2].message.variable_name == "session_id"
    assert messages[2].message.variable_value == "trs_test"
    assert messages[3].message.variable_value is True


def test_agent_preview_is_bounded_and_json_is_complete(monkeypatch):
    payload = {"success": True, "session_id": "trs_test", "result": {"text": "x" * 40000}}
    monkeypatch.setattr(ComposioService, "run", lambda *arguments: payload)
    tool = SearchToolsTool.from_credentials({"api_key": "test-key"})
    messages = list(tool._invoke({}))
    assert len(messages[0].message.json_object["result"]["text"]) == 40000
    assert "truncated" in messages[1].message.text
    assert len(messages[1].message.text) < 32500


def test_tool_error_is_structured_and_has_failure_variable():
    messages = list(SearchToolsTool.from_credentials({})._invoke({}))
    assert messages[0].message.json_object["success"] is False
    assert "API key" in messages[0].message.json_object["error"]
    assert messages[-1].message.variable_value is False


def test_credential_validation_uses_read_only_probe(monkeypatch):
    calls = []

    def request(self, method, path, payload=None):
        calls.append((method, path, payload))
        return {"items": []}

    monkeypatch.setattr(ComposioClient, "request", request)
    ComposioProvider()._validate_credentials({"api_key": "test-key"})
    assert calls == [("GET", "/toolkits?limit=1", None)]


def test_credential_validation_propagates_safe_failure(monkeypatch):
    def request(*arguments):
        raise ComposioError("Composio denied access.")

    monkeypatch.setattr(ComposioClient, "request", request)
    with pytest.raises(ToolProviderCredentialValidationError, match="denied access"):
        ComposioProvider()._validate_credentials({"api_key": "test-key"})
