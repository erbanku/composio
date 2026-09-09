# Privacy

This plugin sends your Composio project API key, trusted user identifier, selected session settings, tool arguments, and requested operations to Composio's fixed HTTPS API. Composio executes authorized actions against the connected third-party services. Remote Workbench/Bash, when explicitly enabled, sends code and its inputs to Composio's sandbox.

Dify provider credentials store the API key. The plugin does not create credential files, log request/response bodies, or send telemetry. Persistent plugin storage contains hashed key/user/session binding identifiers and session capability flags. It does not store the raw API key, raw user ID, OAuth tokens, or app results. The shared HTTP connection pool uses per-request authentication headers and does not cache results.

The plugin removes known token fields and the configured API-key value from outputs, but this is not general-purpose data-loss prevention. Tool arguments, normal action results, error details returned as action data, account identifiers, and short-lived Connect Links may be retained by Dify run history and your model provider. Do not send sensitive data unless your Dify, model, Composio, and downstream-service policies permit it. Share authorization links only with the intended user.

Composio manages app credentials and OAuth refresh. Its retention, processing, and third-party integrations are governed by its policies and your account settings. Closing a session deletes its execution context and sandbox and removes the local binding; it does not delete Dify history or disconnect the user's app accounts. Manage account revocation and data deletion in the relevant services.

No inbound webhook subscriptions, background jobs, automatic action retries, file downloads, or local code execution are started by this plugin. HTTP redirects and implicit environment proxies are disabled. Runtime network access to Composio is necessary even when dependencies are installed offline.
