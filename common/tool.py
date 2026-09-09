import json
from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from common.client import ComposioClient, ComposioError, api_key
from common.service import ComposioService


class ComposioTool(Tool):
    operation: str = ""

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        try:
            client = ComposioClient(api_key(self.runtime.credentials))
            payload = ComposioService(client, self.session.storage).run(self.operation, tool_parameters)
        except ComposioError as error:
            payload = {"success": False, "session_id": "", "error": str(error)}
        except Exception:
            payload = {
                "success": False, "session_id": "",
                "error": "Unexpected plugin failure. Execution may have completed; inspect state before retrying writes.",
            }
        yield self.create_json_message(payload)
        rendered = json.dumps(payload, ensure_ascii=False)
        if len(rendered) > 32000:
            rendered = rendered[:32000] + "\n[Text preview truncated. Complete data is in the JSON output; use smaller pages for agent context.]"
        yield self.create_text_message(rendered)
        yield self.create_variable_message("session_id", payload["session_id"])
        yield self.create_variable_message("success", payload["success"])
