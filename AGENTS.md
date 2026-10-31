# Repository structure and configuration conventions

## Component ownership

- `streamlit-ui/`: Frontend source, API client, frontend settings, frontend tests, dependencies, and setup guide.
- `backend/`: Python API handlers, retrieval/model integration, backend settings, backend tests, and backend dependencies. Add `src/` and `tests/` when code is introduced.
- `infrastructure/`: Serverless service configuration, deployment tooling, lockfile, and CloudFormation definitions under `resources/`.
- `docs/`: Shared architecture and demo documentation; put component-specific setup instructions with that component.
- `data/demo/`: Fictional source documents for demonstrations. Keep answer keys and evaluation guides in `docs/`.
- `.github/workflows/`: CI/CD automation; reference component directories using workflow-level settings.
- `.streamlit/`: Shared repository-level Streamlit theme and secret templates for hosting. Real secrets stay ignored.
- Root files are limited to project-wide documentation, repository conventions, ignore rules, and combined local development requirements. Define dependency versions in component manifests rather than duplicating them at the root.

## Configuration

- All AWS creation and updates must run through GitHub Actions using the checked-in YAML and CloudFormation. Do not run local deployment commands or create/update AWS resources directly. Read-only inspection and local validation are allowed. Destructive cleanup requires an explicit user instruction, such as the authorized rollback of the direct knowledge-base deployment.
- GitHub Actions is the chosen CI/CD provider for this repository. Run deployment only for trusted main-branch code after checks pass, using short-lived AWS OIDC credentials. Provision deployment-role prerequisites through an approved account bootstrap process from checked-in YAML, not ad-hoc local IAM changes.

- Author all infrastructure and CloudFormation resource definitions in YAML (`.yml` or `.yaml`), never JSON. Keep source files under `infrastructure/` and its `resources/` directory.
- Serverless-generated state and build artifacts in `.serverless/` may use JSON. Do not manually edit or rename those tool-managed files; keep them ignored by Git. This exception does not apply to authored infrastructure definitions.
- Define configurable project values once, at the top/global configuration level, before referencing them elsewhere.
- Keep infrastructure defaults in the top-level `Parameters` section of `infrastructure/parameters.yml`. Resource YAML uses native CloudFormation `Ref` and `Fn::Sub`; the optional Serverless entry point imports the same parameter file.
- Keep application configuration in a shared configuration module when application code is added. Pass configuration into functions instead of introducing mutable global state.
- Define workflow settings in workflow-level `env` or reference the project's existing configuration files, such as `infrastructure/.nvmrc`.
- Keep dependency versions in their package manifests and lockfiles. Fixed AWS schema values and policy constants do not need to become configurable variables.
- Read secrets from environment variables or a secret store; never hardcode credentials in global configuration or commit them.
- When moving files, update imports, configuration references, CI working directories, and documentation. Preserve deployed AWS resource identities unless a resource change is explicitly requested.
