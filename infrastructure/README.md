# Infrastructure

All resource definitions are authored in YAML. AWS creation and updates run only through GitHub Actions and CloudFormation. The deployment script rejects local execution; the optional Serverless entry point also blocks local mutations. Local validation and read-only inspection remain allowed.

## Structure

- serverless.yml: Optional Serverless entry point with its own top-level settings and resource imports.
- resources/: S3 storage, managed knowledge base, IAM service role, and log delivery; each template declares its inputs at the top.
- bootstrap/: Account-level GitHub OIDC deployment-role template.
- scripts/: Offline YAML validation and guarded pipeline orchestration.
- tests/: Deployment safety tests.
- package.json and lockfile: Serverless Framework tooling; Node.js version is in .nvmrc.

Generated state may use JSON under .serverless/; it is ignored by Git. The pipeline also exports the compiled CloudFormation template as readable YAML. Do not manually edit generated state.

## Deployment path

1. Push to main, or run the workflow manually on main.
2. GitHub Actions installs dependencies, runs frontend and infrastructure safety tests, and validates YAML.
3. After checks pass, the deployment job assumes the project role with GitHub OIDC.
4. The runner combines the native resource templates and their top-level parameters and validates the CloudFormation template.
5. The runner creates a CloudFormation change set, rejects destructive removals or possible replacements, and executes it. Old API deployment snapshots may leave the stack only with a Retain policy. No Serverless account or license is needed by this deployment path.
6. The job verifies the knowledge base, data source, Lambda, and API mapping, then uploads a YAML template and deployment summary as artifacts.

Pull requests receive checks but cannot assume the deployment role. Concurrent deployments are serialized. Missing settings fail clearly; the job never falls back to local credentials or local deployment.

## One-time account prerequisites

The one-time stack `e-learning-platform-dev-github-bootstrap` provisions `bootstrap/github-deployment-role.yml`. It was created through CloudFormation under an explicit one-time bootstrap exception and reuses the account's existing GitHub OIDC provider. It contains only the project deployment role. All knowledge-base creation and subsequent updates run through GitHub Actions; this does not authorize future local deployments. The bootstrap stack output `DeploymentRoleArn` is configured as the repository variable `AWS_DEPLOY_ROLE_ARN`.

Supply the existing GitHub OIDC provider ARN and exact repository main-branch subject. Some GitHub repositories include immutable owner and repository IDs in that subject. Do not use a wildcard subject or trust another repository. The role scopes deployment to the project stack, source bucket configuration, project artifact prefix, named Bedrock service role, and regional Bedrock/logging resource types. APIs that do not support resource scoping have explicit exceptions. No AdministratorAccess policy is attached, and no source-document reads or model invocations are granted to this deployment role.

Configure these in GitHub repository Settings > Secrets and variables > Actions:

| Type | Name | Value |
| --- | --- | --- |
| Variable | AWS_DEPLOY_ROLE_ARN | DeploymentRoleArn output from the bootstrap stack. |

Do not put credentials in source files or chat. No long-lived AWS access keys or Serverless secrets are required for the CloudFormation pipeline. The optional Serverless tooling is retained for future backend work; invoking Framework v4 separately would require its own authentication. The deployment role retains project artifact-prefix permissions for that tooling.

## Managed knowledge base

The knowledge base uses Bedrock's MANAGED type. Bedrock manages vector storage and indexing; no separate OpenSearch collection is provisioned. Amazon Nova Multimodal Embeddings (amazon.nova-2-multimodal-embeddings-v1:0) uses 1024-dimensional FLOAT32 embeddings and on-demand inference. CUSTOM embedding selection specifies that model within the managed knowledge base.

The S3 connector reads demo/learning/ in e-learning-platform-knowledge-base. SMART_PARSING and omitted chunking settings select the service defaults. No sync schedule is configured; ingestion remains an explicit on-demand operation and is not started on every deployment.

The Bedrock role can list the source prefix, read its objects, invoke the selected model, and publish metrics only in the knowledge base namespace. It has no S3 write/delete or OpenSearch permissions.

The deployment script reads an existing knowledge base ID from CloudFormation outputs and preserves any existing parameter overrides. New parameters use the defaults declared in the resource templates. For first creation only, trust uses a same-account, same-region knowledge-base pattern with a 30-minute expiration. The pipeline immediately deploys again with the new exact ID and verifies trust. Failed initial deployment cannot leave permanent wildcard trust. Deleted IDs are not hardcoded.

Application ingestion logs target /aws/vendedlogs/bedrock/e-learning-platform/dev/knowledge-base, with 30-day retention. These logs describe ingestion and document processing; they are not transcripts of every model or application request.

The stack remains e-learning-platform-dev in us-east-1. Preserve the existing S3 logical IDs and bucket name.

## On-demand operation

Use an approved operational workflow or authorized console sync action. Monitor ingestion to completion and inspect failures. Retrieval uses managedSearchConfiguration; disable reranking unless a supported reranker is explicitly configured. Retrieval and frontend answer generation remain separate backend work.

## Local checks

With Python 3.12 and Node.js 24, install the root requirements and infrastructure/requirements-dev.txt. Run infrastructure/scripts/validate.py, Python unittest discovery under infrastructure/tests, and node --test infrastructure/tests/pipeline-guard.test.cjs. These checks do not deploy resources.

Resource files are template fragments. Their `Metadata.cfn-lint` settings suppress only standalone reference checks that require other fragments; syntax and resource-property checks remain enabled in the editor. The build removes this editor-only metadata, and `scripts/validate.py` runs pinned `cfn-lint` against the complete assembled template and the standalone bootstrap template, locally and in CI. Full-template lint still rejects missing parameters and resources. The generated readable template is saved under the ignored `.serverless/` directory for inspection.
## Reading and changing the configuration

Open the resource template you want to change. Its `Parameters` section at the top defines the inputs used by the resources below it. There is no separate parameter file. `Ref` reads one of those inputs or a resource's ID; `Fn::Sub` builds a name from those values. AWS provides account and region identifiers automatically. The build combines these templates into one stack. Shared inputs such as `ProjectName` and `DeploymentStage` are declared in each template that uses them; conflicting definitions stop the build rather than silently overwriting a setting.

The separate bootstrap stack declares its own inputs at the top because it must work before the application stack exists. Workflow settings are grouped in the workflow-level `env`; Python application settings live in `streamlit-ui/app_config.py`, and deployment paths and waiting limits are grouped at the top of `scripts/deploy.py`. Request-specific results and temporary loop values stay inside their functions. Credentials come from secrets or temporary AWS sessions, never from the global configuration files.

## E-learning Lambda

`resources/lambda.yml` defines the Python 3.14 `E-learning` function, its log group, and its scoped retrieval role. Inputs are declared at the top of that template. It uses `agentic_retrieve_stream` with Nova Pro to return `answer`, `citations`, and `results`, plus temporary project-document source links for Streamlit.

The pipeline stages pinned backend dependencies for Python 3.14 on Linux, packages source plus the SDK, and uploads the encrypted ZIP under the existing project artifact prefix. A content-based key changes only when the package contents change. Unit tests simulate retrieval; the post-deployment `{"action":"health"}` check avoids paid model calls.

The bootstrap deployment-role template includes scoped permissions for the function, its execution role, and its log group. Apply those permissions through the approved bootstrap process before enabling the first Lambda deployment. The application role cannot expand its own access. See the [backend guide](../backend/README.md) for the reference-function coverage, configuration, source-link limits, and document-sync prerequisite.

## REST API

`resources/api-gateway.yml` defines the regional REST API named `e Learning`, its `/e-learning` resource, and the `POST` method. Its settings are at the top of that template. The API uses a custom Lambda integration with the existing `E-learning` function. The `application/json` request mapping sends only the `prompt` field to Lambda; the response mapping returns Lambda's JSON body and its actual HTTP status code. Invalid JSON or a missing/non-text prompt is rejected by the request model. An empty prompt reaches Lambda and returns HTTP 400.

The same GitHub Actions job creates the API and publishes the `dev` stage through CloudFormation. The build derives a deployment snapshot ID from the API definition so mapping changes are published automatically. The API and stage keep stable IDs; previous snapshots are retained. The deployment-role bootstrap adds REST API creation restricted by name and project/stage tags, management restricted by inherited project/stage tags, and invoke-policy management for the existing Lambda only.

After deployment, the pipeline uses API Gateway's `test_invoke_method` with an empty prompt. It expects HTTP 400 and an error body, which checks both mappings and Lambda permissions without invoking a model. The stack output `LearningApiUrl` is the full POST URL for Streamlit's `API_URL` setting. This demo route currently accepts requests without sign-in or an API key.

Send `Content-Type: application/json` with a body such as `{"prompt":"When should I submit my weekly hours?"}`. A successful request returns `answer`, `citations`, `results`, and `sources`. Documents must be synced before the response can cite them. The REST integration waits up to 29 seconds; Lambda retains its separate 120-second execution limit. A longer-running request can therefore return HTTP 504 at the gateway even while Lambda is still running.
