"""GitHub Actions-only deployment orchestration; resource definitions stay in YAML."""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError
from botocore.exceptions import WaiterError
import yaml


# Global configuration; infrastructure defaults are defined in parameters.yml.
INFRASTRUCTURE_DIRECTORY = Path(__file__).resolve().parents[1]
ARTIFACT_DIRECTORY = INFRASTRUCTURE_DIRECTORY / ".serverless"
CONFIGURATION_FILE = INFRASTRUCTURE_DIRECTORY / "parameters.yml"
RESOURCE_FILES = ("knowledge-base-storage.yml", "knowledge-base.yml", "knowledge-base-logging.yml")
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


def describe_stack(client, stack_name):
    """Return existing stack metadata, distinguishing an absent stack from access errors."""
    try:
        stack = client.describe_stacks(StackName=stack_name)["Stacks"][0]
    except ClientError as error:
        if (error.response["Error"]["Code"] == "ValidationError"
                and "does not exist" in error.response["Error"]["Message"]):
            return None
        raise
    if stack["StackStatus"].endswith("_IN_PROGRESS"):
        raise RuntimeError("The stack already has an operation in progress.")
    return stack


def stack_outputs(client, stack_name):
    """Read output values after a stack operation has finished."""
    stack = describe_stack(client, stack_name)
    return {item["OutputKey"]: item["OutputValue"] for item in (stack or {}).get("Outputs", [])}


def build_template():
    """Merge native YAML sections without resolving CloudFormation intrinsics locally."""
    template = {"AWSTemplateFormatVersion": "2010-09-09",
                "Description": "E-learning knowledge base, document storage, and application logging"}
    for path in [CONFIGURATION_FILE] + [INFRASTRUCTURE_DIRECTORY / "resources" / p for p in RESOURCE_FILES]:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        for section, entries in document.items():
            destination = template.setdefault(section, {})
            duplicates = destination.keys() & entries.keys()
            if duplicates:
                raise ValueError(f"Duplicate {section} definitions: {sorted(duplicates)}")
            destination.update(entries)
    return template


def validate_changes(changes):
    """Prevent automatic removal or possible replacement of existing resources."""
    for change in changes:
        resource = change["ResourceChange"]
        print(resource["Action"], resource["LogicalResourceId"], resource["ResourceType"])
        if resource["Action"] == "Remove" or resource.get("Replacement", "False") != "False":
            raise RuntimeError("Resource removal or replacement needs a separately reviewed migration.")


def package_and_deploy(client, stack_name, trust_id, exists=True):
    """Validate YAML and execute a checked change set, only from the trusted pipeline."""
    require_pipeline()
    template = build_template()
    ARTIFACT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    readable = ARTIFACT_DIRECTORY / "cloudformation-template-update-stack.yml"
    body = yaml.safe_dump(template, sort_keys=False)
    readable.write_text(body, encoding="utf-8")
    client.validate_template(TemplateBody=body)
    expires_at = (
        datetime.now(timezone.utc) + timedelta(minutes=BOOTSTRAP_MINUTES)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    overrides = {"DeploymentStage": STAGE, "KnowledgeBaseTrustResourceId": trust_id,
                 "KnowledgeBaseBootstrapTrustExpiresAt": expires_at if trust_id == "*" else "1970-01-01T00:00:00Z"}
    parameters = [{"ParameterKey": key, "ParameterValue": value}
                  for key, value in overrides.items()]
    # Preserve deployed overrides on subsequent updates; new parameters use YAML defaults.
    if exists:
        previous = client.describe_stacks(StackName=stack_name)["Stacks"][0]
        parameters += [{"ParameterKey": p["ParameterKey"], "UsePreviousValue": True}
                       for p in previous.get("Parameters", [])
                       if p["ParameterKey"] in template["Parameters"] and p["ParameterKey"] not in overrides]
    change_set = client.create_change_set(
        StackName=stack_name, ChangeSetName=f"github-{uuid4().hex}",
        ChangeSetType="UPDATE" if exists else "CREATE", TemplateBody=body,
        Parameters=parameters, Capabilities=["CAPABILITY_NAMED_IAM"],
        Tags=[{"Key": "Project", "Value": template["Parameters"]["ProjectName"]["Default"]}],
    )["Id"]
    try:
        client.get_waiter("change_set_create_complete").wait(
            ChangeSetName=change_set, WaiterConfig={"Delay": 3, "MaxAttempts": 120})
    except WaiterError:
        result = client.describe_change_set(ChangeSetName=change_set)
        reason = result.get("StatusReason", "")
        if result["Status"] == "FAILED" and ("didn't contain changes" in reason or "No updates are to be performed" in reason):
            client.delete_change_set(ChangeSetName=change_set)
            print("CloudFormation: no infrastructure changes.")
            return
        raise RuntimeError(f"CloudFormation change set failed: {reason}")
    pages = client.get_paginator("describe_change_set").paginate(ChangeSetName=change_set)
    changes = [change for page in pages for change in page.get("Changes", [])]
    validate_changes(changes)
    client.execute_change_set(ChangeSetName=change_set)
    waiter = "stack_update_complete" if exists else "stack_create_complete"
    try:
        client.get_waiter(waiter).wait(StackName=stack_name, WaiterConfig={"Delay": 5, "MaxAttempts": 480})
    except WaiterError:
        for event in client.describe_stack_events(StackName=stack_name)["StackEvents"][:20]:
            print(event["LogicalResourceId"], event["ResourceStatus"], event.get("ResourceStatusReason", ""))
        raise


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
    project_name = build_template()["Parameters"]["ProjectName"]["Default"]
    stack_name = f"{project_name}-{STAGE}"
    cloudformation = boto3.client("cloudformation", region_name=REGION)
    previous_stack = describe_stack(cloudformation, stack_name)
    previous = {item["OutputKey"]: item["OutputValue"] for item in (previous_stack or {}).get("Outputs", [])}
    trust_id = previous.get("LearningKnowledgeBaseId", "*")
    package_and_deploy(cloudformation, stack_name, trust_id, exists=previous_stack is not None)
    deployed = stack_outputs(cloudformation, stack_name)
    exact_id = deployed["LearningKnowledgeBaseId"]
    if trust_id != exact_id:
        # First deployment uses expiring trust; immediately remove the wildcard.
        package_and_deploy(cloudformation, stack_name, exact_id)
        deployed = stack_outputs(cloudformation, stack_name)
    verify(deployed)


if __name__ == "__main__":
    main()
