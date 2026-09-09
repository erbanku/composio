import httpx
import pytest

from common import client as client_module
from common.client import ComposioClient, ComposioError, api_key


@pytest.mark.parametrize("status", [301, 400, 401, 403, 404, 409, 422, 429, 500, 503])
def test_http_errors_are_sanitized_and_not_retried(status):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, headers={"location": "https://attacker.example/"}, text="private-key and customer data")

    with httpx.Client(transport=httpx.MockTransport(handle)) as transport:
        with pytest.raises(ComposioError) as error:
            ComposioClient("private-key", transport).request("POST", "/tool_router/session", {})
    assert f"HTTP {status}" in str(error.value)
    assert "private-key" not in str(error.value)
    assert "customer" not in str(error.value)
    assert len(calls) == 1


def test_timeout_is_an_unknown_outcome_without_retry():
    calls = []

    def handle(request):
        calls.append(request)
        raise httpx.ReadTimeout("private-key", request=request)

    with httpx.Client(transport=httpx.MockTransport(handle)) as transport:
        with pytest.raises(ComposioError, match="unknown") as error:
            ComposioClient("private-key", transport).request("POST", "/tool_router/session/trs_test/execute", {})
    assert "private-key" not in str(error.value)
    assert len(calls) == 1


@pytest.mark.parametrize("content", [b"not JSON private-key", b"[]", b"null", b"\xff"])
def test_invalid_response_is_safe(content):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=content))) as transport:
        with pytest.raises(ComposioError) as error:
            ComposioClient("private-key", transport).request("GET", "/toolkits")
    assert "private-key" not in str(error.value)


def test_response_size_limit(monkeypatch):
    monkeypatch.setattr(client_module, "MAX_RESPONSE_BYTES", 16)
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"value": "x" * 100}))) as transport:
        with pytest.raises(ComposioError, match="exceeded"):
            ComposioClient("private-key", transport).request("GET", "/toolkits")


def test_credentials_are_per_request_not_client_defaults():
    keys = []

    def handle(request):
        keys.append(request.headers["x-api-key"])
        return httpx.Response(200, json={})

    with httpx.Client(transport=httpx.MockTransport(handle)) as transport:
        for key in ["project-one", "project-two", "project-one"]:
            ComposioClient(key, transport).request("GET", "/toolkits")
        assert "x-api-key" not in transport.headers
    assert keys == ["project-one", "project-two", "project-one"]


@pytest.mark.parametrize("key", [None, "", " ", "foo\nbar", "foo bar", "é", "a" * 4097, "foo\x00bar"])
def test_invalid_api_keys(key):
    with pytest.raises(ComposioError):
        api_key({"api_key": key})


def test_key_is_trimmed():
    assert api_key({"api_key": "  project-key\n"}) == "project-key"
