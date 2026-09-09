import json
from typing import Any

import httpx


BASE_URL = "https://backend.composio.dev/api/v3.1"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
HTTP = httpx.Client(
    timeout=httpx.Timeout(120, connect=10, pool=10),
    limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
    follow_redirects=False,
    trust_env=False,
)


class ComposioError(ValueError):
    pass


def api_key(credentials: dict[str, Any]) -> str:
    value = credentials.get("api_key")
    if not isinstance(value, str) or not value.strip():
        raise ComposioError("Configure a Composio project API key in the provider credentials.")
    value = value.strip()
    if len(value) > 4096 or not value.isascii() or any(ord(character) < 33 or ord(character) == 127 for character in value):
        raise ComposioError("The Composio API key contains invalid characters.")
    return value


class ComposioClient:
    def __init__(self, key: str, transport: httpx.Client | None = None):
        self.key = key
        self.http = transport if transport is not None else HTTP

    def request(self, method: str, path: str, payload: dict | None = None) -> dict:
        try:
            with self.http.stream(
                method, BASE_URL + path,
                headers={"x-api-key": self.key, "Accept": "application/json"}, json=payload,
            ) as response:
                if not 200 <= response.status_code < 300:
                    messages = {
                        400: "Invalid Composio request. Check the current tool schema and session settings.",
                        401: "Composio rejected the API key. Update provider credentials.",
                        403: "Composio denied access. Check project API-key permissions and account ownership.",
                        404: "Composio session, toolkit, or tool was not found. Check the ID or search again.",
                        409: "Composio reported a conflict. Check the account connection and current state.",
                        422: "Composio rejected the input. Match the current schema exactly.",
                        429: "Composio rate limit reached. Wait before a deliberate retry.",
                    }
                    message = messages.get(response.status_code, "Composio request failed.")
                    if response.status_code >= 500:
                        message += " Execution outcome may be unknown; inspect state before retrying writes."
                    raise ComposioError(f"{message} HTTP {response.status_code}.")
                chunks = bytearray()
                for chunk in response.iter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > MAX_RESPONSE_BYTES:
                        raise ComposioError(
                            "Composio response exceeded 8 MiB. Use smaller pages or workbench offloading. "
                            "Execution may have completed; do not repeat writes blindly."
                        )
                try:
                    result = json.loads(chunks)
                except (ValueError, UnicodeError):
                    raise ComposioError(
                        "Composio returned invalid JSON. Execution outcome is unknown; check before retrying."
                    ) from None
                if not isinstance(result, dict):
                    raise ComposioError("Composio returned an unexpected response shape; check execution state.")
                return result
        except httpx.HTTPError:
            raise ComposioError(
                "Composio network request failed or timed out. Execution outcome may be unknown. "
                "No automatic retry was attempted; check state before repeating writes."
            ) from None
