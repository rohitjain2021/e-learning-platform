# Infrastructure

This component owns AWS deployment configuration, CloudFormation resource definitions, and Node.js deployment tooling.

- `serverless.yml`: Service entry point and global project settings.
- `resources/`: CloudFormation resource definitions imported by the service.
- `package.json` and `package-lock.json`: Serverless Framework tooling and pinned dependencies.
- `.nvmrc`: Node.js version for deployment tooling.

From the repository root, using Node.js 24 and configured AWS and Serverless credentials:

```powershell
Push-Location infrastructure
npm ci
npx serverless print
npx serverless deploy --stage dev --region us-east-1
Pop-Location
```

Moving the configuration into this folder does not change the service, stage, region, stack name, or bucket name. For `dev` in `us-east-1`, the stack remains `e-learning-platform-dev` and the source bucket remains `e-learning-platform-knowledge-base`. Do not rename resource logical IDs to reorganize files.

Keep application code outside this component. When adding Lambda functions, reference and package the backend component explicitly; the frontend dependencies must not be included in Lambda artifacts.
