# Project configuration convention

- Define configurable project values once, at the top/global configuration level, before referencing them elsewhere.
- Keep infrastructure settings in the top-level `custom` section of `serverless.yml`. Reference those settings from provider configuration and imported resource files instead of repeating literal values.
- Keep application configuration in a shared configuration module when application code is added. Pass configuration into functions instead of introducing mutable global state.
- Define workflow settings in workflow-level `env` or reference the project's existing configuration files, such as `.nvmrc`.
- Keep dependency versions in their package manifests and lockfiles. Fixed AWS schema values and policy constants do not need to become configurable variables.
- Read secrets from environment variables or a secret store; never hardcode credentials in global configuration or commit them.
