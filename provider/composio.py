from typing import Any

from dify_plugin import ToolProvider
from dify_plugin.errors.tool import ToolProviderCredentialValidationError

from common.client import ComposioClient, ComposioError, api_key


class ComposioProvider(ToolProvider):
    def _validate_credentials(self, credentials: dict[str, Any]) -> None:
        try:
            ComposioClient(api_key(credentials)).request("GET", "/toolkits?limit=1")
        except ComposioError as error:
            raise ToolProviderCredentialValidationError(str(error)) from None
