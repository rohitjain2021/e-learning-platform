# Backend

Keep the Python API handlers, retrieval and model integration, shared backend configuration, and backend tests in this component. The AWS SDK dependency is declared here. Add backend source under `src/` and its tests under `tests/` when implementing the API; keep the Streamlit UI in its own component.

Deployment configuration belongs in `infrastructure/`. Global infrastructure values belong at the top of its Serverless configuration; future backend settings should be defined once in a shared backend configuration module.

The frontend API contract and connection instructions are documented in the [frontend guide](../streamlit-ui/README.md).
