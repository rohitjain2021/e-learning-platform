"""GitHub Actions-only deployment orchestration; resource definitions stay in YAML."""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess

import boto3
from botocore.exceptions import ClientError
import yaml


# Global configuration; component resource settings remain in serverless.yml.
INFRASTRUCTURE_DIRECTORY = Path(__file__).resolve().parents[1]
ARTIFACT_DIRECTORY = INFRASTRUCTURE_DIRECTORY / ".serverless"
CONFIGURATION_FILE = INFRASTRUCTURE_DIRECTORY / "serverless.yml"
EXPECTED_REPOSITORY = "rohitjain2021/e-learning-platform"
EXPECTED_REF = "refs/heads/main"
BOOTSTRAP_MINUTES = 30
REGION = os.environ.get("AWS_REGION", "us-east-1")
STAGE = os.environ.get("DEPLOYMENT_STAGE", "dev")


def require_pipeline():
    if (os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("GITHUB_REPOSITORY") != EXPECTED_REPOSITORY
            or os.environ.get("GITHUB_REF") != EXPECTED_REF):
        raise RuntimeError("Deployment is allowed only in this repository's main-branch GitHub Actions pipeline.")
    if not (os.environ.get("SERVERLESS_ACCESS_KEY") or os.environ.get("SERVERLESS_LICENSE_KEY")):
        raise RuntimeError("Configure SERVERLESS_ACCESS_KEY or SERVERLESS_LICENSE_KEY in GitHub Actions secrets.")


def stack_outputs(client, stack_name):
    try:
        stack = client.describe_stacks(StackName=stack_name)["Stacks"][0]
    except ClientError as error:
        if (error.response["Error"]["Code"] == "ValidationError"
                and "does not exist" in error.response["Error"]["Message"]):
            return {}
        raise
    if stack["StackStatus"].endswith("_IN_PROGRESS"):
        raise RuntimeError("The stack already has an operation in progress.")
    return {item["OutputKey"]: item["OutputValue"] for item in stack.get("Outputs", [])}


def package_and_deploy(client, trust_id):
    environment = dict(os.environ)
    environment["KNOWLEDGE_BASE_TRUST_ID"] = trust_id
    environment["KNOWLEDGE_BASE_BOOTSTRAP_EXPIRES_AT"] = (
        datetime.now(timezone.utc) + timedelta(minutes=BOOTSTRAP_MINUTES)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    environment["CI"] = "true"
    base = ["npx", "--no-install", "serverless"]
    options = ["--stage", STAGE, "--region", REGION]
    subprocess.run(base + ["package"] + options, cwd=INFRASTRUCTURE_DIRECTORY,
                   env=environment, check=True)
    compiled = ARTIFACT_DIRECTORY / "cloudformation-template-update-stack.json"
    template = json.loads(compiled.read_text(encoding="utf-8"))
    readable = ARTIFACT_DIRECTORY / "cloudformation-template-update-stack.yml"
    readable.write_text(yaml.safe_dump(template, sort_keys=False), encoding="utf-8")
    client.validate_template(TemplateBody=readable.read_text(encoding="utf-8"))
    subprocess.run(base + ["deploy", "--package", ".serverless"] + options,
                   cwd=INFRASTRUCTURE_DIRECTORY, env=environment, check=True)


def verify(outputs):
    knowledge_base_id = outputs["LearningKnowledgeBaseId"]
    role_arn = outputs["LearningKnowledgeBaseServiceRoleArn"]
    role = boto3.client("iam").get_role(RoleName=role_arn.split("/")[-1])["Role"]
    statements = role["AssumeRolePolicyDocument"]["Statement"]
    expected_arn = outputs["LearningKnowledgeBaseArn"]
    expected_account = expected_arn.split(":")[4]
    for statement in statements:
        conditions = statement.get("Condition", {})
        source_arn = conditions.get("ArnEquals", conditions.get("ArnLike", {})).get("aws:SourceArn")
        if (statement.get("Effect") != "Allow" or source_arn != expected_arn
                or conditions.get("StringEquals", {}).get("aws:SourceAccount") != expected_account
                or statement.get("Principal") != {"Service": "bedrock.amazonaws.com"}):
            raise RuntimeError("Knowledge base role trust is not restricted to the exact resource.")
    if len(statements) != 1:
        raise RuntimeError("Unexpected knowledge base role trust statements.")
    agent = boto3.client("bedrock-agent", region_name=REGION)
    kb = agent.get_knowledge_base(knowledgeBaseId=knowledge_base_id)["knowledgeBase"]
    source = agent.get_data_source(
        knowledgeBaseId=knowledge_base_id,
        dataSourceId=outputs["LearningKnowledgeBaseDataSourceId"],
    )["dataSource"]
    if kb["status"] != "ACTIVE" or source["status"] != "AVAILABLE":
        raise RuntimeError("Knowledge base or data source is not ready.")
    connector = source["dataSourceConfiguration"]["managedKnowledgeBaseConnectorConfiguration"]
    if connector.get("syncSchedule"):
        raise RuntimeError("Data source must use on-demand synchronization.")
    summary = dict(outputs, KnowledgeBaseStatus=kb["status"], DataSourceStatus=source["status"])
    (ARTIFACT_DIRECTORY / "deployment-summary.yml").write_text(
        yaml.safe_dump(summary, sort_keys=False), encoding="utf-8")
    print(f"Verified knowledge base {knowledge_base_id}; exact-resource trust; manual sync.")


def main():
    require_pipeline()
    project_name = yaml.safe_load(CONFIGURATION_FILE.read_text(encoding="utf-8"))["custom"]["projectName"]
    stack_name = f"{project_name}-{STAGE}"
    cloudformation = boto3.client("cloudformation", region_name=REGION)
    previous = stack_outputs(cloudformation, stack_name)
    trust_id = previous.get("LearningKnowledgeBaseId", "*")
    package_and_deploy(cloudformation, trust_id)
    deployed = stack_outputs(cloudformation, stack_name)
    exact_id = deployed["LearningKnowledgeBaseId"]
    if trust_id != exact_id:
        # First deployment uses expiring trust; immediately remove the wildcard.
        package_and_deploy(cloudformation, exact_id)
        deployed = stack_outputs(cloudformation, stack_name)
    verify(deployed)


if __name__ == "__main__":
    main()
