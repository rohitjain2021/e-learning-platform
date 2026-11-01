"""GitHub Actions-only deployment orchestration; resource definitions stay in YAML."""

import os
import hashlib
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

import boto3
from botocore.exceptions import ClientError
from botocore.exceptions import WaiterError
import yaml


# Global paths: keep generated files separate from the YAML people edit.
INFRASTRUCTURE_DIRECTORY = Path(__file__).resolve().parents[1]
ARTIFACT_DIRECTORY = INFRASTRUCTURE_DIRECTORY / ".serverless"
RESOURCE_FILES = ("knowledge-base-storage.yml", "knowledge-base.yml", "knowledge-base-logging.yml", "lambda.yml", "api-gateway.yml")
# API Gateway snapshots are immutable; a changed API definition needs a new deployment ID.
API_DEPLOYMENT_PREFIX = "LearningApiDeployment"
# Package only reviewed backend source files; tests, secrets, and local environments stay out.
BACKEND_SOURCE_DIRECTORY = INFRASTRUCTURE_DIRECTORY.parent / "backend" / "src"
BACKEND_SOURCE_FILES = ("lambda.py",)
ZIP_TIMESTAMP = (2020, 1, 1, 0, 0, 0)
# Match the pinned SDK used by our unit tests instead of relying on Lambda's bundled SDK version.
BACKEND_DEPENDENCY_DIRECTORY = ARTIFACT_DIRECTORY / "backend-dependencies"
# Fixed trust boundaries: a caller must not be able to choose a different trusted repository.
EXPECTED_REPOSITORY = "rohitjain2021/e-learning-platform"
EXPECTED_REF = "refs/heads/main"
# Global deployment controls: bound temporary access and how long AWS operations may run.
BOOTSTRAP_MINUTES = 30
CHANGE_SET_POLL_SECONDS = 3
CHANGE_SET_POLL_ATTEMPTS = 120
STACK_POLL_SECONDS = 5
STACK_POLL_ATTEMPTS = 480
FAILURE_EVENT_LIMIT = 20
# Define this script's defaults here; GitHub Actions can supply region and stage at runtime.
EXPIRED_TRUST_TIMESTAMP = "1970-01-01T00:00:00Z"
REGION = os.environ.get("AWS_REGION", "us-east-1")
STAGE = os.environ.get("DEPLOYMENT_STAGE", "dev")


def require_pipeline():
    """Reject local or untrusted runs before any AWS resource can be changed."""
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
                "Description": "E-learning knowledge base, Lambda, REST API, storage, and logging"}
    for path in [INFRASTRUCTURE_DIRECTORY / "resources" / p for p in RESOURCE_FILES]:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        # Fragment editor exceptions must never suppress checks on the full template.
        metadata = document.get("Metadata", {})
        metadata.pop("cfn-lint", None)
        if "Metadata" in document and not metadata:
            del document["Metadata"]
        for section, entries in document.items():
            destination = template.setdefault(section, {})
            duplicates = destination.keys() & entries.keys()
            # Each template declares its own inputs. Shared input names must agree exactly.
            conflicts = {key for key in duplicates if section != "Parameters" or destination[key] != entries[key]}
            if conflicts:
                raise ValueError(f"Conflicting or duplicate {section} definitions: {sorted(conflicts)}")
            destination.update(entries)
        if path.name == "api-gateway.yml":
            # Change only the snapshot's name; the API, resource, and stage keep their identities.
            digest = hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()[:12]
            deployment_id = API_DEPLOYMENT_PREFIX + digest
            resources = template["Resources"]
            resources[deployment_id] = resources.pop(API_DEPLOYMENT_PREFIX)
    return template


def validate_changes(changes):
    """Block destructive changes; allow old API snapshots to leave the stack only if retained."""
    for change in changes:
        resource = change["ResourceChange"]
        print(resource["Action"], resource["LogicalResourceId"], resource["ResourceType"])
        # Old immutable snapshots stay in AWS; only the stage switches to a new snapshot.
        if (resource["Action"] == "Remove" and resource.get("PolicyAction") == "Retain"
                and resource["ResourceType"] == "AWS::ApiGateway::Deployment"
                and resource["LogicalResourceId"].startswith(API_DEPLOYMENT_PREFIX)):
            continue
        if resource["Action"] == "Remove" or resource.get("Replacement", "False") != "False":
            raise RuntimeError("Resource removal or replacement needs a separately reviewed migration.")


def package_backend(template, previous_stack=None):
    """Upload a deterministic application ZIP with its pinned SDK from GitHub Actions to the private artifact bucket."""
    require_pipeline()
    values = {name: item.get("Default") for name, item in template["Parameters"].items()}
    values.update({item["ParameterKey"]: item["ParameterValue"]
                   for item in (previous_stack or {}).get("Parameters", [])})
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        for name in BACKEND_SOURCE_FILES:
            entry = ZipInfo(name, date_time=ZIP_TIMESTAMP)
            entry.compress_type = ZIP_DEFLATED
            entry.external_attr = 0o644 << 16
            archive.writestr(entry, (BACKEND_SOURCE_DIRECTORY / name).read_bytes())
        # CI installs the pinned backend manifest into this ignored staging directory.
        if not BACKEND_DEPENDENCY_DIRECTORY.is_dir():
            raise RuntimeError("Install the pinned backend dependencies before packaging the Lambda.")
        for path in sorted(BACKEND_DEPENDENCY_DIRECTORY.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                relative = path.relative_to(BACKEND_DEPENDENCY_DIRECTORY).as_posix()
                if relative in BACKEND_SOURCE_FILES:
                    raise RuntimeError("A dependency would overwrite application source.")
                entry = ZipInfo(relative, date_time=ZIP_TIMESTAMP)
                entry.compress_type = ZIP_DEFLATED
                entry.external_attr = 0o644 << 16
                archive.writestr(entry, path.read_bytes())
    package = buffer.getvalue()
    digest = hashlib.sha256(package).hexdigest()
    key = f"serverless/{values['ProjectName']}/{STAGE}/lambda/{digest}.zip"
    # Content-based keys ensure CloudFormation updates code when source contents change.
    boto3.client("s3", region_name=REGION).put_object(
        Bucket=values["DeploymentArtifactBucketName"], Key=key, Body=package,
        ContentType="application/zip", ServerSideEncryption="AES256")
    print("Uploaded the encrypted Lambda source and pinned SDK package.")
    return key


def package_and_deploy(client, stack_name, trust_id, exists=True, artifact_key=None):
    """Validate YAML and execute a checked change set, only from the trusted pipeline."""
    require_pipeline()
    if not artifact_key:
        raise RuntimeError("The pipeline must package the Lambda source before deploying.")
    # Assemble a readable template and ask AWS to validate it before requesting changes.
    template = build_template()
    ARTIFACT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    readable = ARTIFACT_DIRECTORY / "cloudformation-template-update-stack.yml"
    body = yaml.safe_dump(template, sort_keys=False)
    readable.write_text(body, encoding="utf-8")
    client.validate_template(TemplateBody=body)
    expires_at = (
        datetime.now(timezone.utc) + timedelta(minutes=BOOTSTRAP_MINUTES)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    overrides = {"DeploymentStage": STAGE, "LambdaArtifactKey": artifact_key, "KnowledgeBaseTrustResourceId": trust_id,
                 "KnowledgeBaseBootstrapTrustExpiresAt": expires_at if trust_id == "*" else EXPIRED_TRUST_TIMESTAMP}
    parameters = [{"ParameterKey": key, "ParameterValue": value}
                  for key, value in overrides.items()]
    # Preserve deployed overrides on subsequent updates; new parameters use YAML defaults.
    if exists:
        previous = client.describe_stacks(StackName=stack_name)["Stacks"][0]
        parameters += [{"ParameterKey": p["ParameterKey"], "UsePreviousValue": True}
                       for p in previous.get("Parameters", [])
                       if p["ParameterKey"] in template["Parameters"] and p["ParameterKey"] not in overrides]
    # A change set previews AWS's planned actions before the pipeline executes them.
    change_set = client.create_change_set(
        StackName=stack_name, ChangeSetName=f"github-{uuid4().hex}",
        ChangeSetType="UPDATE" if exists else "CREATE", TemplateBody=body,
        Parameters=parameters, Capabilities=["CAPABILITY_NAMED_IAM"],
        Tags=[{"Key": "Project", "Value": template["Parameters"]["ProjectName"]["Default"]}],
    )["Id"]
    try:
        client.get_waiter("change_set_create_complete").wait(
            ChangeSetName=change_set, WaiterConfig={"Delay": CHANGE_SET_POLL_SECONDS, "MaxAttempts": CHANGE_SET_POLL_ATTEMPTS})
    except WaiterError:
        result = client.describe_change_set(ChangeSetName=change_set)
        reason = result.get("StatusReason", "")
        # Re-running unchanged infrastructure is a successful no-op, not a failed deployment.
        if result["Status"] == "FAILED" and ("didn't contain changes" in reason or "No updates are to be performed" in reason):
            client.delete_change_set(ChangeSetName=change_set)
            print("CloudFormation: no infrastructure changes.")
            return
        raise RuntimeError(f"CloudFormation change set failed: {reason}")
    # Inspect every page so a risky removal cannot be missed in a large change set.
    pages = client.get_paginator("describe_change_set").paginate(ChangeSetName=change_set)
    changes = [change for page in pages for change in page.get("Changes", [])]
    validate_changes(changes)
    # Apply only the checked change set, then wait for CloudFormation to finish.
    client.execute_change_set(ChangeSetName=change_set)
    waiter = "stack_update_complete" if exists else "stack_create_complete"
    try:
        client.get_waiter(waiter).wait(StackName=stack_name, WaiterConfig={"Delay": STACK_POLL_SECONDS, "MaxAttempts": STACK_POLL_ATTEMPTS})
    except WaiterError:
        # Include recent AWS failure reasons so operators know which resource needs attention.
        for event in client.describe_stack_events(StackName=stack_name)["StackEvents"][:FAILURE_EVENT_LIMIT]:
            print(event["LogicalResourceId"], event["ResourceStatus"], event.get("ResourceStatusReason", ""))
        raise


def verify(outputs):
    """Confirm AWS created a ready knowledge base with narrowly scoped access and manual sync."""
    # Check the actual service-role trust, not just what the source template requested.
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
    # A completed stack must also have a ready knowledge base and document connection.
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
    # Confirm the starter function is ready and returns the expected basic response.
    functions = boto3.client("lambda", region_name=REGION)
    function_name = outputs["LearningLambdaFunctionName"]
    configuration = functions.get_function_configuration(FunctionName=function_name)
    if (configuration["State"] != "Active" or configuration["LastUpdateStatus"] != "Successful"
            or configuration["Runtime"] != outputs["LearningLambdaRuntime"]):
        raise RuntimeError("The Lambda function is not ready on the configured runtime.")
    invocation = functions.invoke(FunctionName=function_name, Payload=b'{"action":"health"}')
    result = json.loads(invocation["Payload"].read())
    if invocation.get("FunctionError") or result.get("statusCode") != 200:
        raise RuntimeError("Lambda readiness invocation failed.")
    if json.loads(result["body"]).get("status") != "ready":
        raise RuntimeError("Lambda did not return its readiness response.")
    # Exercise the actual request/response mappings without paying for a model invocation.
    gateway = boto3.client("apigateway", region_name=REGION)
    test = gateway.test_invoke_method(
        restApiId=outputs["LearningRestApiId"], resourceId=outputs["LearningApiResourceId"],
        httpMethod="POST", headers={"Content-Type": "application/json"}, body='{"prompt":""}')
    if test["status"] != 400 or "error" not in json.loads(test["body"]):
        raise RuntimeError("API Gateway did not preserve Lambda's invalid-prompt response.")
    print("Verified POST /e-learning mapping: empty prompt returned HTTP 400.")
    # Save readable deployment evidence for the workflow artifact, without credentials.
    summary = dict(outputs, KnowledgeBaseStatus=kb["status"], DataSourceStatus=source["status"], LambdaState=configuration["State"])
    (ARTIFACT_DIRECTORY / "deployment-summary.yml").write_text(
        yaml.safe_dump(summary, sort_keys=False), encoding="utf-8")
    print(f"Verified knowledge base {knowledge_base_id}; exact-resource trust; manual sync.")


def main():
    """Deploy from GitHub Actions, tighten first-creation trust, and verify the final result."""
    require_pipeline()
    project_name = build_template()["Parameters"]["ProjectName"]["Default"]
    stack_name = f"{project_name}-{STAGE}"
    cloudformation = boto3.client("cloudformation", region_name=REGION)
    # Reuse the deployed ID when updating, so later runs never broaden the role's trust.
    previous_stack = describe_stack(cloudformation, stack_name)
    previous = {item["OutputKey"]: item["OutputValue"] for item in (previous_stack or {}).get("Outputs", [])}
    artifact_key = package_backend(build_template(), previous_stack)
    trust_id = previous.get("LearningKnowledgeBaseId", "*")
    package_and_deploy(cloudformation, stack_name, trust_id, exists=previous_stack is not None, artifact_key=artifact_key)
    deployed = stack_outputs(cloudformation, stack_name)
    exact_id = deployed["LearningKnowledgeBaseId"]
    if trust_id != exact_id:
        # First deployment uses expiring trust; immediately remove the wildcard.
        package_and_deploy(cloudformation, stack_name, exact_id, artifact_key=artifact_key)
        deployed = stack_outputs(cloudformation, stack_name)
    verify(deployed)


if __name__ == "__main__":
    main()
