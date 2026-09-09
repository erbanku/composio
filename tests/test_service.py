import json

import httpx
import pytest

from common.client import ComposioClient, ComposioError
from common.service import META_TOOLS, ComposioService, json_input, scrub, successful


KEY = "test-project-secret"
USER = "user-123"
SESSION = "trs_test123"
PARAMETERS = {"user_id": USER, "session_id": SESSION}


class MemoryStorage:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values[key]

    def set(self, key, value):
        self.values[key] = value

    def delete(self, key):
        del self.values[key]


@pytest.fixture
def harness():
    requests = []
    responses = []
    storage = MemoryStorage()

    def handle(request):
        requests.append(request)
        assert request.headers["x-api-key"] == KEY
        assert request.url.host == "backend.composio.dev"
        assert request.url.path.startswith("/api/v3.1/")
        if request.url.path == "/api/v3.1/tool_router/session":
            return httpx.Response(201, json={
                "session_id": SESSION, "tool_router_tools": sorted(META_TOOLS),
                "mcp": {"url": "https://secret.example/mcp"},
            })
        return httpx.Response(200, json=responses.pop(0) if responses else {"data": {}, "error": None})

    with httpx.Client(transport=httpx.MockTransport(handle)) as transport:
        service = ComposioService(ComposioClient(KEY, transport), storage)
        yield service, storage, requests, responses


def create(harness, **parameters):
    service = harness[0]
    return service.run("create_session", {"user_id": USER, **parameters})


def body(request):
    return json.loads(request.content)


def test_create_defaults_and_private_binding(harness):
    result = create(harness, toolkits="gmail, github,gmail")
    service, storage, requests, _ = harness
    assert len(requests) == 1
    assert result["success"] is True
    assert result["session_id"] == SESSION
    assert "mcp" not in json.dumps(result)
    payload = body(requests[0])
    assert payload["user_id"] == USER
    assert payload["toolkits"] == {"enable": ["gmail", "github"]}
    assert payload["workbench"] == {"enable": False, "enable_proxy_execution": False}
    assert payload["manage_connections"]["enable_connection_removal"] is False
    assert KEY not in repr(storage.values)
    assert USER not in repr(storage.values)
    assert service.binding(USER, SESSION)["workbench"] is False


def test_advanced_config_preserved(harness):
    config = {
        "auth_configs": {"gmail": "ac_test"},
        "connected_accounts": {"gmail": ["ca_test"]},
        "tags": ["readOnlyHint"],
        "multi_account": {"enable": True, "require_explicit_selection": True},
    }
    create(harness, config=json.dumps(config))
    payload = body(harness[2][0])
    assert all(payload[name] == value for name, value in config.items())


@pytest.mark.parametrize("config", [
    {"user_id": "victim"}, {"toolkits": ["gmail"]}, {"experimental": {}},
    {"workbench": True}, {"workbench": {"enable": "false"}},
    {"workbench": {"enable_proxy_execution": True}}, {"auth_configs": []},
    {"manage_connections": {"callback_url": "http://example.com"}},
])
def test_invalid_config_fails_before_network(harness, config):
    with pytest.raises(ComposioError):
        create(harness, config=config)
    assert not harness[2]


@pytest.mark.parametrize("user_id", ["", "default", "anonymous", "null", "undefined", "a" * 257, 123])
def test_user_id_is_required_and_not_shared(harness, user_id):
    with pytest.raises(ComposioError):
        harness[0].run("create_session", {"user_id": user_id})
    assert not harness[2]


def test_search_reuses_session_and_native_meta_route(harness):
    create(harness)
    result = harness[0].run("search_tools", {**PARAMETERS, "query": "search unread email"})
    request = harness[2][-1]
    assert request.url.path.endswith(f"/{SESSION}/execute_meta")
    assert body(request) == {
        "slug": "COMPOSIO_SEARCH_TOOLS", "arguments": {"queries": [{"use_case": "search unread email"}]},
    }
    assert result["session_id"] == SESSION
    assert len(harness[2]) == 2


@pytest.mark.parametrize("change", [{"user_id": "another-user"}, {"session_id": "trs_foreign"}])
def test_foreign_user_or_session_denied_without_network(harness, change):
    create(harness)
    with pytest.raises(ComposioError, match="not bound"):
        harness[0].run("search_tools", {**PARAMETERS, **change, "query": "read mail"})
    assert len(harness[2]) == 1


def test_api_key_rotation_requires_new_binding(harness):
    create(harness)
    harness[0].client.key = "different-project-key"
    with pytest.raises(ComposioError, match="not bound"):
        harness[0].run("search_tools", {**PARAMETERS, "query": "read mail"})
    assert len(harness[2]) == 1


@pytest.mark.parametrize("session_id", ["../other", "trs_x?admin=1", "a/b", "", "a" * 257])
def test_path_injection_is_rejected(harness, session_id):
    with pytest.raises(ComposioError):
        harness[0].run("inspect_session", {**PARAMETERS, "session_id": session_id, "view": "tools"})
    assert not harness[2]


def test_schemas_and_connection_link(harness):
    create(harness)
    service, _, requests, responses = harness
    service.run("get_tool_schemas", {**PARAMETERS, "tool_slugs": "GMAIL_FETCH_EMAILS,GMAIL_FETCH_EMAILS"})
    assert body(requests[-1])["arguments"] == {
        "tool_slugs": ["GMAIL_FETCH_EMAILS"], "include": ["input_schema", "output_schema"],
    }
    responses.append({"redirect_url": "https://connect.composio.dev/link/test", "link_token": "private", "connected_account_id": "ca_test"})
    result = service.run("connect_toolkit", {**PARAMETERS, "toolkit": "gmail", "callback_url": "https://app.example/return"})
    assert requests[-1].url.path.endswith("/link")
    assert body(requests[-1]) == {"toolkit": "gmail", "callback_url": "https://app.example/return"}
    assert "link_token" not in result["result"]
    assert result["result"]["redirect_url"].startswith("https://connect.")


@pytest.mark.parametrize("callback", ["http://example.com", "javascript:alert(1)", "https://user:pass@example.com", "https:///bad"])
def test_unsafe_callback_is_rejected(harness, callback):
    create(harness)
    with pytest.raises(ComposioError, match="HTTPS"):
        harness[0].run("connect_toolkit", {**PARAMETERS, "toolkit": "gmail", "callback_url": callback})
    assert len(harness[2]) == 1


def test_direct_execution_preserves_arguments_and_account(harness):
    create(harness)
    harness[0].run("execute_tool", {
        **PARAMETERS, "tool_slug": "GMAIL_FETCH_EMAILS",
        "arguments": '{"query":"is:unread","max_results":5}', "account": "ca_work",
    })
    request = harness[2][-1]
    assert request.url.path.endswith("/execute")
    assert body(request) == {
        "tool_slug": "GMAIL_FETCH_EMAILS", "arguments": {"query": "is:unread", "max_results": 5}, "account": "ca_work",
    }


@pytest.mark.parametrize("slug", ["COMPOSIO_REMOTE_BASH_TOOL", "not valid", "../../etc", "gmail_fetch"])
def test_direct_route_cannot_bypass_meta_guards(harness, slug):
    create(harness)
    with pytest.raises(ComposioError):
        harness[0].run("execute_tool", {**PARAMETERS, "tool_slug": slug})
    assert len(harness[2]) == 1


def test_batch_parallel_contract_and_partial_failure(harness):
    create(harness)
    harness[3].append({"data": {"data": {"error_count": 1, "success_count": 1, "results": [
        {"index": 0, "response": {"successful": True, "data": {"id": "ok"}}},
        {"index": 1, "error": "app authorization needed"},
    ]}, "successful": True}, "error": None})
    tools = [{"tool_slug": "GMAIL_FETCH_EMAILS", "arguments": {}}, {"tool_slug": "GITHUB_GET_THE_AUTHENTICATED_USER", "arguments": {}}]
    result = harness[0].run("execute_batch", {**PARAMETERS, "tools": json.dumps(tools)})
    assert body(harness[2][-1]) == {
        "slug": "COMPOSIO_MULTI_EXECUTE_TOOL", "arguments": {"tools": tools, "sync_response_to_workbench": False},
    }
    assert result["success"] is False
    assert result["result"]["data"]["data"]["success_count"] == 1
    assert len(harness[2]) == 2


@pytest.mark.parametrize("tools", [[], [{}], ["bad"], [{"tool_slug": "GMAIL_FETCH_EMAILS", "arguments": []}],
    [{"tool_slug": "COMPOSIO_REMOTE_BASH_TOOL", "arguments": {}}],
    [{"tool_slug": "GMAIL_FETCH_EMAILS", "arguments": {}}] * 51])
@pytest.mark.parametrize("operation", ["execute_batch", "execute_meta_tool"])
def test_invalid_batch_rejected_on_both_routes(harness, tools, operation):
    create(harness)
    parameters = {**PARAMETERS, "tools": tools, "slug": "COMPOSIO_MULTI_EXECUTE_TOOL", "arguments": {"tools": tools}}
    with pytest.raises(ComposioError):
        harness[0].run(operation, parameters)
    assert len(harness[2]) == 1


def test_remote_workbench_requires_explicit_opt_in(harness):
    create(harness)
    parameters = {**PARAMETERS, "slug": "COMPOSIO_REMOTE_WORKBENCH", "arguments": {"code": "print(1)"}}
    with pytest.raises(ComposioError, match="disabled"):
        harness[0].run("execute_meta_tool", parameters)
    create(harness, config={"workbench": {"enable": True}})
    assert harness[0].run("execute_meta_tool", parameters)["success"] is True
    assert body(harness[2][-1])["slug"] == "COMPOSIO_REMOTE_WORKBENCH"


def test_meta_session_argument_cannot_override_binding(harness):
    create(harness)
    with pytest.raises(ComposioError, match="must match"):
        harness[0].run("execute_meta_tool", {
            **PARAMETERS, "slug": "COMPOSIO_SEARCH_TOOLS", "arguments": {"session_id": "trs_foreign"},
        })
    assert len(harness[2]) == 1


def test_inspection_paginates_and_encodes_cursor(harness):
    create(harness)
    harness[0].run("inspect_session", {**PARAMETERS, "view": "toolkits", "cursor": "a+b/=&other=x", "toolkit": "gmail"})
    query = harness[2][-1].url.params
    assert query["cursor"] == "a+b/=&other=x"
    assert query["toolkits"] == "gmail"
    assert query["limit"] == "50"
    assert "other" not in query


def test_close_removes_binding_after_remote_delete(harness):
    create(harness)
    harness[3].append({"deleted": True, "session_id": SESSION})
    result = harness[0].run("close_session", PARAMETERS)
    assert result["success"] is True
    assert harness[2][-1].method == "DELETE"
    assert not harness[1].values


def test_storage_failure_does_not_expose_internal_errors(harness, monkeypatch):
    def fail(*arguments):
        raise RuntimeError(KEY)

    monkeypatch.setattr(harness[1], "set", fail)
    with pytest.raises(ComposioError, match="could not store") as error:
        create(harness)
    assert KEY not in str(error.value)
    assert len(harness[2]) == 1


def test_close_keeps_binding_when_deletion_is_unconfirmed(harness):
    create(harness)
    harness[3].append({"deleted": False, "session_id": SESSION})
    with pytest.raises(ComposioError, match="did not confirm"):
        harness[0].run("close_session", PARAMETERS)
    assert harness[1].values


@pytest.mark.parametrize("value", ['[]', 'null', '{bad', '{"x":NaN}', '{"x":1e999}', {"x": float("inf")}, "x" * (256 * 1024 + 1)])
def test_json_rejects_wrong_shape_and_nonfinite_values(value):
    with pytest.raises(ComposioError):
        json_input({"arguments": value}, "arguments")


@pytest.mark.parametrize("result", [
    {"error": "failed"}, {"successful": False}, {"data": {"success": False}},
    {"data": {"data": {"error_count": 1}}},
    {"results": [{"response": {"successful": False}}]},
])
def test_failure_envelopes_are_not_successful(result):
    assert not successful(result)


def test_known_secrets_are_removed_but_action_data_is_preserved():
    assert scrub({"data": [{"access_token": "secret", "message": KEY, "id": "record"}]}, KEY) == {
        "data": [{"message": "[REDACTED]", "id": "record"}],
    }
