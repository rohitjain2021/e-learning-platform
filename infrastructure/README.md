# Infrastructure

All resource definitions are authored in YAML. AWS creation and updates run only through GitHub Actions and CloudFormation. The deployment script rejects local execution; the optional Serverless entry point also blocks local mutations. Local validation and read-only inspection remain allowed.

## Structure

- parameters.yml: Global CloudFormation parameter defaults.
- serverless.yml: Optional Serverless Framework entry point importing the same native YAML.
- resources/: S3 storage, managed knowledge base, IAM service role, and log delivery.
- bootstrap/: Account-level GitHub OIDC deployment-role template.
- scripts/: Offline YAML validation and guarded pipeline orchestration.
- tests/: Deployment safety tests.
- package.json and lockfile: Serverless Framework tooling; Node.js version is in .nvmrc.

Generated state may use JSON under .serverless/; it is ignored by Git. The pipeline also exports the compiled CloudFormation template as readable YAML. Do not manually edit generated state.

## Deployment path

1. Push to main, or run the workflow manually on main.
2. GitHub Actions installs dependencies, runs frontend and infrastructure safety tests, and validates YAML.
3. After checks pass, the deployment job assumes the project role with GitHub OIDC.
4. The runner merges the native parameter and resource YAML and validates the CloudFormation template.
5. The runner creates a CloudFormation change set, rejects removals or possible replacements, and executes it. No Serverless account or license is needed by this deployment path.
6. The job verifies the knowledge base, data source, and exact-resource service-role trust, then uploads a YAML template and deployment summary as artifacts.

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

The deployment script reads an existing knowledge base ID from CloudFormation outputs and preserves any existing parameter overrides. New parameters use the global YAML defaults. For first creation only, trust uses a same-account, same-region knowledge-base pattern with a 30-minute expiration. The pipeline immediately deploys again with the new exact ID and verifies trust. Failed initial deployment cannot leave permanent wildcard trust. Deleted IDs are not hardcoded.

Application ingestion logs target /aws/vendedlogs/bedrock/e-learning-platform/dev/knowledge-base, with 30-day retention. These logs describe ingestion and document processing; they are not transcripts of every model or application request.

The stack remains e-learning-platform-dev in us-east-1. Preserve the existing S3 logical IDs and bucket name.

## On-demand operation

Use an approved operational workflow or authorized console sync action. Monitor ingestion to completion and inspect failures. Retrieval uses managedSearchConfiguration; disable reranking unless a supported reranker is explicitly configured. Retrieval and frontend answer generation remain separate backend work.

## Local checks

With Python 3.12 and Node.js 24, install the root requirements and infrastructure/requirements-dev.txt. Run infrastructure/scripts/validate.py, Python unittest discovery under infrastructure/tests, and node --test infrastructure/tests/pipeline-guard.test.cjs. These checks do not deploy resources.