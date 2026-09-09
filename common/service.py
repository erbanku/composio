import hashlib
import json
import re
from typing import Any
from urllib.parse import urlencode, urlsplit

from common.client import ComposioClient, ComposioError


IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,256}\Z")
APP_SLUG = re.compile(r"[A-Z][A-Z0-9_]{1,199}\Z")
MAX_INPUT_BYTES = 256 * 1024
META_TOOLS = frozenset({
    "COMPOSIO_SEARCH_TOOLS", "COMPOSIO_GET_TOOL_SCHEMAS", "COMPOSIO_MANAGE_CONNECTIONS",
    "COMPOSIO_MULTI_EXECUTE_TOOL", "COMPOSIO_REMOTE_WORKBENCH", "COMPOSIO_REMOTE_BASH_TOOL",
})
REMOTE_TOOLS = frozenset({"COMPOSIO_REMOTE_WORKBENCH", "COMPOSIO_REMOTE_BASH_TOOL"})
CONFIG_KEYS = frozenset({
    "auth_configs", "connected_accounts", "tools", "tags", "workbench", "multi_account",
    "manage_connections", "preload", "search", "execute",
})


def text(parameters: dict, name: str, required: bool = True) -> str:
    value = parameters.get(name)
    if value is None or value == "":
        if required:
            raise ComposioError(f"{name} is required.")
        return ""
    if not isinstance(value, str) or not value.strip():
        raise ComposioError(f"{name} must be a nonempty string.")
    value = value.strip()
    if len(value.encode("utf-8")) > MAX_INPUT_BYTES:
        raise ComposioError(f"{name} exceeds the 256 KiB input limit.")
    return value


def identifier(value: str, name: str) -> str:
    if not IDENTIFIER.fullmatch(value):
        raise ComposioError(f"{name} must contain only letters, digits, underscores, or hyphens (1-256).")
    return value


def reject_constant(value: str) -> None:
    raise ValueError("Non-finite JSON number")


def json_input(parameters: dict, name: str, expected: type = dict) -> Any:
    raw = parameters.get(name)
    if raw is None or raw == "":
        raw = "{}" if expected is dict else "[]"
    try:
        if isinstance(raw, str):
            if len(raw.encode("utf-8")) > MAX_INPUT_BYTES:
                raise ValueError("Input too large")
            value = json.loads(raw, parse_constant=reject_constant)
        else:
            value = raw
        if not isinstance(value, expected):
            raise ValueError("Wrong JSON type")
        if len(json.dumps(value, allow_nan=False).encode("utf-8")) > MAX_INPUT_BYTES:
            raise ValueError("Input too large")
        return value
    except (ValueError, TypeError, RecursionError, OverflowError):
        kind = "object" if expected is dict else "array"
        raise ComposioError(f"{name} must be a valid JSON {kind} of at most 256 KiB, with finite numbers.") from None


def csv_list(value: str, name: str) -> list[str]:
    result = list(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))
    if not result or len(result) > 100:
        raise ComposioError(f"{name} must contain between 1 and 100 comma-separated identifiers.")
    return [identifier(item, name) for item in result]


def successful(result: dict) -> bool:
    if result.get("error") or result.get("successful") is False or result.get("success") is False:
        return False
    if isinstance(result.get("error_count"), (int, float)) and result["error_count"] > 0:
        return False
    data = result.get("data")
    if isinstance(data, dict) and not successful(data):
        return False
    results = result.get("results")
    if isinstance(results, list):
        for item in results:
            if isinstance(item, dict):
                if not successful(item):
                    return False
                response = item.get("response")
                if isinstance(response, dict) and not successful(response):
                    return False
    return True


def scrub(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return {
            name: scrub(item, key) for name, item in value.items()
            if name.lower() not in {"api_key", "x-api-key", "access_token", "refresh_token", "link_token", "mcp"}
        }
    if isinstance(value, list):
        return [scrub(item, key) for item in value]
    if isinstance(value, str):
        return value.replace(key, "[REDACTED]")
    return value


class ComposioService:
    def __init__(self, client: ComposioClient, storage: Any):
        self.client = client
        self.storage = storage

    def binding_key(self, user_id: str, session_id: str) -> str:
        identity = json.dumps([self.client.key, user_id, session_id], ensure_ascii=True)
        return "composio:" + hashlib.sha256(identity.encode()).hexdigest()

    def binding(self, user_id: str, session_id: str) -> dict:
        try:
            value = self.storage.get(self.binding_key(user_id, session_id))
            binding = json.loads(value)
            if not isinstance(binding, dict) or not isinstance(binding.get("meta_tools"), list):
                raise ValueError("Invalid binding")
            return binding
        except Exception:
            raise ComposioError(
                "Session is not bound to this API key and user in Dify, or plugin storage is unavailable. "
                "Use Create Session with the same trusted user_id; external sessions cannot be imported."
            ) from None

    def create(self, parameters: dict, user_id: str) -> dict:
        config = json_input(parameters, "config")
        if set(config) - CONFIG_KEYS:
            raise ComposioError("Unsupported session config key. Use the documented Advanced Session JSON fields.")
        for name in CONFIG_KEYS - {"tags"}:
            if name in config and not isinstance(config[name], dict):
                raise ComposioError(f"config.{name} must be an object.")
        workbench = {"enable": False, "enable_proxy_execution": False, **config.get("workbench", {})}
        if not isinstance(workbench["enable"], bool) or not isinstance(workbench["enable_proxy_execution"], bool):
            raise ComposioError("workbench enable and enable_proxy_execution must be booleans.")
        if workbench["enable_proxy_execution"] and not workbench["enable"]:
            raise ComposioError("Enable workbench before enabling its proxy execution.")
        connections = {"enable": True, "enable_connection_removal": False, **config.get("manage_connections", {})}
        callback = connections.get("callback_url")
        if callback:
            self.validate_callback(callback)
        payload = {**config, "user_id": user_id, "workbench": workbench, "manage_connections": connections}
        toolkits = text(parameters, "toolkits", required=False)
        if toolkits:
            payload["toolkits"] = {"enable": csv_list(toolkits, "toolkits")}
        response = self.client.request("POST", "/tool_router/session", payload)
        if not successful(response):
            return response
        session_id = identifier(text(response, "session_id"), "session_id")
        meta_tools = response.get("tool_router_tools")
        if not isinstance(meta_tools, list) or not all(isinstance(item, str) for item in meta_tools):
            raise ComposioError("Composio session response omitted the available tool list. Create was not retried.")
        binding = {"meta_tools": meta_tools, "workbench": workbench["enable"]}
        try:
            self.storage.set(self.binding_key(user_id, session_id), json.dumps(binding).encode())
        except Exception:
            raise ComposioError(
                "Composio created a session, but Dify could not store its user binding. "
                "Check plugin storage permission/capacity before creating another session."
            ) from None
        return {
            "session_id": session_id, "available_meta_tools": meta_tools,
            "workbench_enabled": workbench["enable"],
            "instructions": "Reuse this session_id. Search, connect if needed, then execute. Confirm writes first.",
        }

    @staticmethod
    def validate_callback(value: str) -> None:
        try:
            parsed = urlsplit(value)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("Invalid URL")
        except (ValueError, TypeError, AttributeError):
            raise ComposioError("callback_url must be an HTTPS URL without embedded credentials.") from None

    @staticmethod
    def validate_app_slug(value: str) -> str:
        if not APP_SLUG.fullmatch(value) or value.startswith("COMPOSIO_"):
            raise ComposioError("Use an exact discovered app tool slug; meta tools require Execute Meta Tool.")
        return value

    def validate_batch(self, arguments: dict) -> None:
        tools = arguments.get("tools")
        if not isinstance(tools, list) or not 1 <= len(tools) <= 50:
            raise ComposioError("A batch must contain 1-50 independent app actions.")
        for item in tools:
            if not isinstance(item, dict) or set(item) - {"tool_slug", "arguments", "account"}:
                raise ComposioError("Each batch item supports only tool_slug, arguments, and optional account.")
            self.validate_app_slug(text(item, "tool_slug"))
            if not isinstance(item.get("arguments"), dict):
                raise ComposioError("Each batch item requires an arguments object.")
            if "account" in item:
                text(item, "account")

    def meta(self, path: str, binding: dict, slug: str, arguments: dict) -> dict:
        if "session_id" in arguments and arguments["session_id"] != path.rsplit("/", 1)[-1]:
            raise ComposioError("Meta arguments.session_id must match the current session_id.")
        if slug not in META_TOOLS or slug not in binding["meta_tools"]:
            raise ComposioError("Meta tool is unsupported or unavailable in this session. Inspect session tools.")
        if slug in REMOTE_TOOLS and not binding.get("workbench"):
            raise ComposioError("Remote execution is disabled. Explicitly enable workbench in Create Session settings.")
        if slug == "COMPOSIO_MULTI_EXECUTE_TOOL":
            self.validate_batch(arguments)
        return self.client.request("POST", path + "/execute_meta", {"slug": slug, "arguments": arguments})

    def run(self, operation: str, parameters: dict) -> dict:
        user_id = text(parameters, "user_id")
        if len(user_id) > 256 or user_id.lower() in {"default", "anonymous", "null", "undefined"}:
            raise ComposioError("Use a stable, unique authenticated user_id of at most 256 characters, not a shared default.")
        if operation == "create_session":
            result = self.create(parameters, user_id)
            session_id = result.get("session_id", "")
        else:
            session_id = identifier(text(parameters, "session_id"), "session_id")
            binding = self.binding(user_id, session_id)
            path = "/tool_router/session/" + session_id
            if operation == "search_tools":
                result = self.meta(path, binding, "COMPOSIO_SEARCH_TOOLS", {
                    "queries": [{"use_case": text(parameters, "query")}],
                })
            elif operation == "get_tool_schemas":
                result = self.meta(path, binding, "COMPOSIO_GET_TOOL_SCHEMAS", {
                    "tool_slugs": csv_list(text(parameters, "tool_slugs"), "tool_slugs"),
                    "include": ["input_schema", "output_schema"],
                })
            elif operation == "connect_toolkit":
                payload = {"toolkit": identifier(text(parameters, "toolkit"), "toolkit")}
                callback = text(parameters, "callback_url", required=False)
                if callback:
                    self.validate_callback(callback)
                    payload["callback_url"] = callback
                result = self.client.request("POST", path + "/link", payload)
            elif operation == "execute_tool":
                payload = {
                    "tool_slug": self.validate_app_slug(text(parameters, "tool_slug")),
                    "arguments": json_input(parameters, "arguments"),
                }
                account = text(parameters, "account", required=False)
                if account:
                    payload["account"] = account
                result = self.client.request("POST", path + "/execute", payload)
            elif operation == "execute_batch":
                result = self.meta(path, binding, "COMPOSIO_MULTI_EXECUTE_TOOL", {
                    "tools": json_input(parameters, "tools", list), "sync_response_to_workbench": False,
                })
            elif operation == "inspect_session":
                view = text(parameters, "view")
                if view not in {"tools", "toolkits"}:
                    raise ComposioError("view must be tools or toolkits.")
                query = {"limit": "50"}
                cursor = text(parameters, "cursor", required=False)
                if cursor:
                    query["cursor"] = cursor
                toolkit = text(parameters, "toolkit", required=False)
                if toolkit:
                    if view != "toolkits":
                        raise ComposioError("toolkit filter is available only for the toolkits view.")
                    query["toolkits"] = identifier(toolkit, "toolkit")
                result = self.client.request("GET", path + "/" + view + "?" + urlencode(query))
            elif operation == "close_session":
                result = self.client.request("DELETE", path)
                if successful(result):
                    if result.get("deleted") is not True or result.get("session_id") != session_id:
                        raise ComposioError("Composio did not confirm session deletion. Local binding was retained.")
                    try:
                        self.storage.delete(self.binding_key(user_id, session_id))
                    except Exception:
                        result["storage_warning"] = "Remote session deleted; local binding cleanup failed."
            elif operation == "execute_meta_tool":
                result = self.meta(path, binding, text(parameters, "slug"), json_input(parameters, "arguments"))
            else:
                raise ComposioError("Unknown Composio operation.")
        return {"success": successful(result), "session_id": session_id, "result": scrub(result, self.client.key)}
