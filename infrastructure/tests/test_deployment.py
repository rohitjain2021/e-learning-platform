"""Check deployment safety without making AWS calls."""

import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from tempfile import TemporaryDirectory

from botocore.exceptions import ClientError, WaiterError


# Load the deployment module without running its main function or contacting AWS.
SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "deploy.py"
SPEC = importlib.util.spec_from_file_location("project_deploy", SCRIPT_PATH)
DEPLOY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DEPLOY)


class DeploymentSafetyTests(unittest.TestCase):
    """Use simulated AWS clients to check deployment boundaries and template validation safely."""
    def test_local_execution_is_rejected(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "GitHub Actions"):
                DEPLOY.require_pipeline()

    def test_pull_request_or_other_repository_cannot_deploy(self):
        for repo, ref in ((DEPLOY.EXPECTED_REPOSITORY, "refs/pull/1/merge"),
                          ("someone/another-repository", DEPLOY.EXPECTED_REF)):
            with self.subTest(repo=repo, ref=ref), patch.dict(os.environ, {
                "GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": repo, "GITHUB_REF": ref,
            }, clear=True):
                with self.assertRaises(RuntimeError):
                    DEPLOY.require_pipeline()

    def test_trusted_pipeline_does_not_require_serverless_credentials(self):
        with patch.dict(os.environ, {
            "GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": DEPLOY.EXPECTED_REPOSITORY,
            "GITHUB_REF": DEPLOY.EXPECTED_REF,
        }, clear=True):
            DEPLOY.require_pipeline()

    def test_template_is_native_cloudformation_with_retained_source_bucket(self):
        import yaml
        template = DEPLOY.build_template()
        self.assertNotIn("${self:", yaml.safe_dump(template))
        self.assertEqual(template["Resources"]["KnowledgeBaseBucket"]["DeletionPolicy"], "Retain")
        self.assertEqual(template["Resources"]["LearningKnowledgeBase"]["Properties"]["KnowledgeBaseConfiguration"]["Type"], "MANAGED")
        self.assertEqual(template["Parameters"]["EmbeddingDimensions"]["Default"], 1024)
        self.assertEqual(template["Parameters"]["EmbeddingModelId"]["Default"], "amazon.nova-2-multimodal-embeddings-v1:0")

    def test_each_template_declares_the_parameters_it_uses(self):
        """Ensure template inputs are defined at the top of the file that references them."""
        import re
        import yaml
        parameter_names = set(DEPLOY.build_template()["Parameters"])
        for name in DEPLOY.RESOURCE_FILES:
            path = DEPLOY.INFRASTRUCTURE_DIRECTORY / "resources" / name
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            resource_text = yaml.safe_dump({k: v for k, v in document.items() if k != "Parameters"})
            references = set(re.findall(r"Ref: ([A-Za-z0-9]+)", resource_text))
            references.update(re.findall(r"\$\{([A-Za-z0-9]+)\}", resource_text))
            with self.subTest(template=name):
                self.assertTrue((references & parameter_names).issubset(document["Parameters"]))

    def test_conflicting_shared_parameter_definitions_are_rejected(self):
        """Stop composition if two templates disagree about a shared input."""
        import shutil
        import yaml
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "resources").mkdir()
            for name in DEPLOY.RESOURCE_FILES:
                shutil.copyfile(DEPLOY.INFRASTRUCTURE_DIRECTORY / "resources" / name, root / "resources" / name)
            path = root / "resources" / "knowledge-base.yml"
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            document["Parameters"]["ProjectName"]["Default"] = "different-project"
            path.write_text(yaml.safe_dump(document), encoding="utf-8")
            with patch.object(DEPLOY, "INFRASTRUCTURE_DIRECTORY", root):
                with self.assertRaisesRegex(ValueError, "Conflicting.*Parameters"):
                    DEPLOY.build_template()

    def test_full_template_lint_still_catches_broken_cross_file_references(self):
        from cfnlint.api import lint
        import yaml
        template = DEPLOY.build_template()
        self.assertNotIn("cfn-lint", template.get("Metadata", {}))
        template["Resources"]["KnowledgeBaseBucket"]["Properties"]["BucketName"] = {"Ref": "MissingBucketParameter"}
        matches = lint(yaml.safe_dump(template), config={"regions": [DEPLOY.REGION]})
        self.assertIn("E1020", {match.rule.id for match in matches})

    def test_fragment_editor_exceptions_do_not_hide_invalid_resource_properties(self):
        from cfnlint.api import lint
        import yaml
        path = DEPLOY.INFRASTRUCTURE_DIRECTORY / "resources" / "knowledge-base-storage.yml"
        fragment = yaml.safe_load(path.read_text(encoding="utf-8"))
        fragment["Resources"]["KnowledgeBaseBucket"]["Properties"]["InvalidBucketSetting"] = True
        matches = lint(yaml.safe_dump(fragment), config={"regions": [DEPLOY.REGION]})
        self.assertIn("E3002", {match.rule.id for match in matches})

    def test_removals_and_possible_replacements_are_blocked(self):
        for details in ({"Action": "Remove"}, {"Action": "Modify", "Replacement": "True"},
                        {"Action": "Modify", "Replacement": "Conditional"}):
            with self.subTest(details=details), self.assertRaisesRegex(RuntimeError, "migration"):
                DEPLOY.validate_changes([{"ResourceChange": {
                    "LogicalResourceId": "KnowledgeBaseBucket", "ResourceType": "AWS::S3::Bucket", **details}}])

    def test_api_snapshot_rotation_keeps_old_snapshots_and_blocks_deletion(self):
        """Updating a route can publish a snapshot without authorizing resource destruction."""
        change = {"Action": "Remove", "LogicalResourceId": "LearningApiDeployment123abc",
                  "ResourceType": "AWS::ApiGateway::Deployment", "PolicyAction": "Retain"}
        DEPLOY.validate_changes([{"ResourceChange": change}])
        with self.assertRaises(RuntimeError):
            DEPLOY.validate_changes([{"ResourceChange": dict(change, PolicyAction="Delete")}])

    def test_api_changes_publish_a_new_snapshot_without_changing_the_api_or_stage(self):
        """A changed mapping must reach the deployed URL, not just the API editor."""
        import shutil
        import yaml
        before = DEPLOY.build_template()
        original = before["Resources"]["LearningApiStage"]["Properties"]["DeploymentId"]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(DEPLOY.INFRASTRUCTURE_DIRECTORY / "resources", root / "resources")
            path = root / "resources" / "api-gateway.yml"
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            document["Resources"]["LearningPostMethod"]["Properties"]["Integration"]["RequestTemplates"]["application/json"] += '\n'
            path.write_text(yaml.safe_dump(document), encoding="utf-8")
            with patch.object(DEPLOY, "INFRASTRUCTURE_DIRECTORY", root):
                after = DEPLOY.build_template()
        self.assertNotEqual(original, after["Resources"]["LearningApiStage"]["Properties"]["DeploymentId"])
        self.assertEqual(before["Resources"]["LearningRestApi"], after["Resources"]["LearningRestApi"])
        deployment = after["Resources"][after["Resources"]["LearningApiStage"]["Properties"]["DeploymentId"]["Ref"]]
        self.assertIn("LearningPostMethod", deployment["DependsOn"])
        self.assertEqual(deployment["DeletionPolicy"], "Retain")

    def test_native_deployment_is_also_blocked_locally(self):
        from unittest.mock import Mock
        client = Mock()
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(RuntimeError, "GitHub Actions"):
            DEPLOY.package_and_deploy(client, "example-stack", "*", artifact_key="serverless/project/dev/lambda/test.zip")
        client.create_change_set.assert_not_called()

    def test_backend_packaging_is_blocked_outside_the_pipeline(self):
        """Local validation must not upload application artifacts to AWS."""
        with patch.dict(os.environ, {}, clear=True), patch.object(DEPLOY.boto3, "client") as client:
            with self.assertRaisesRegex(RuntimeError, "GitHub Actions"):
                DEPLOY.package_backend(DEPLOY.build_template())
        client.assert_not_called()

    def test_backend_zip_contains_source_and_pinned_dependencies(self):
        """Build a repeatable ZIP containing source and only the staged production dependencies."""
        import io
        from unittest.mock import Mock
        from zipfile import ZipFile
        s3 = Mock()
        environment = {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": DEPLOY.EXPECTED_REPOSITORY,
                       "GITHUB_REF": DEPLOY.EXPECTED_REF}
        with TemporaryDirectory() as directory:
            dependencies = Path(directory)
            (dependencies / "sdk_test.py").write_text("# Simulated pinned dependency", encoding="utf-8")
            with patch.object(DEPLOY, "BACKEND_DEPENDENCY_DIRECTORY", dependencies), patch.dict(os.environ, environment, clear=True), patch.object(DEPLOY.boto3, "client", return_value=s3):
                first = DEPLOY.package_backend(DEPLOY.build_template())
                second = DEPLOY.package_backend(DEPLOY.build_template())
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("serverless/e-learning-platform/dev/lambda/"))
        arguments = s3.put_object.call_args.kwargs
        self.assertEqual(arguments["ServerSideEncryption"], "AES256")
        with ZipFile(io.BytesIO(arguments["Body"])) as package:
            self.assertEqual(package.namelist(), ["lambda.py", "sdk_test.py"])
            self.assertEqual(package.read("lambda.py"), (DEPLOY.BACKEND_SOURCE_DIRECTORY / "lambda.py").read_bytes())

    def test_lambda_execution_role_scopes_documents_knowledge_base_and_model(self):
        """Keep all new retrieval permissions limited to the project and chosen inference profile."""
        resources = DEPLOY.build_template()["Resources"]
        role = resources["LearningLambdaServiceRole"]["Properties"]
        statements = role["Policies"][0]["PolicyDocument"]["Statement"]
        self.assertEqual(len(statements), 1)
        self.assertEqual(statements[0]["Action"], ["logs:CreateLogStream", "logs:PutLogEvents"])
        self.assertEqual(statements[0]["Resource"], {"Fn::GetAtt": ["LearningLambdaLogGroup", "Arn"]})
        retrieval = {item["Sid"]: item for item in role["Policies"][1]["PolicyDocument"]["Statement"]}
        self.assertEqual(retrieval["ReadOnlyProjectKnowledgeBase"]["Resource"], {"Fn::GetAtt": ["LearningKnowledgeBase", "KnowledgeBaseArn"]})
        self.assertEqual(retrieval["StartRegionalAgenticRetrieval"]["Condition"]["StringEquals"]["aws:RequestedRegion"], {"Ref": "AWS::Region"})
        self.assertEqual(retrieval["OpenProjectSourceDocuments"]["Action"], "s3:GetObject")
        self.assertIn("bedrock:InferenceProfileArn", retrieval["UseProfileDestinationModels"]["Condition"]["ArnEquals"])
        self.assertNotIn("LearningLambdaFunctionUrl", resources)

    def test_lambda_filename_handler_and_two_minute_limit_match(self):
        """Keep packaging, handler configuration, and the requested execution limit in agreement."""
        template = DEPLOY.build_template()
        self.assertEqual(template["Parameters"]["LambdaHandler"]["Default"], "lambda.lambda_handler")
        self.assertEqual(template["Parameters"]["LambdaTimeoutSeconds"]["Default"], 120)
        self.assertEqual(DEPLOY.BACKEND_SOURCE_FILES, ("lambda.py",))

    def test_change_set_preserves_overrides_and_executes_without_serverless(self):
        from unittest.mock import Mock
        client = Mock()
        client.describe_stacks.return_value = {"Stacks": [{"Parameters": [
            {"ParameterKey": "KnowledgeBaseSourcePrefix", "ParameterValue": "existing/prefix/"}]}]}
        client.create_change_set.return_value = {"Id": "example-change-set"}
        client.get_paginator.return_value.paginate.return_value = [{"Changes": [{"ResourceChange": {
            "Action": "Add", "LogicalResourceId": "LearningKnowledgeBase", "ResourceType": "AWS::Bedrock::KnowledgeBase"}}]}]
        environment = {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": DEPLOY.EXPECTED_REPOSITORY,
                       "GITHUB_REF": DEPLOY.EXPECTED_REF}
        with TemporaryDirectory() as directory, patch.object(DEPLOY, "ARTIFACT_DIRECTORY", Path(directory)), patch.dict(os.environ, environment, clear=True):
            DEPLOY.package_and_deploy(client, "example-stack", "*", artifact_key="serverless/project/dev/lambda/test.zip")
        arguments = client.create_change_set.call_args.kwargs
        self.assertIn({"ParameterKey": "KnowledgeBaseSourcePrefix", "UsePreviousValue": True}, arguments["Parameters"])
        self.assertEqual(arguments["ChangeSetType"], "UPDATE")
        self.assertEqual(arguments["Capabilities"], ["CAPABILITY_NAMED_IAM"])
        client.execute_change_set.assert_called_once_with(ChangeSetName="example-change-set")

    def test_empty_change_set_is_a_successful_no_op(self):
        from unittest.mock import Mock
        client = Mock()
        client.describe_stacks.return_value = {"Stacks": [{}]}
        client.create_change_set.return_value = {"Id": "example-change-set"}
        client.get_waiter.return_value.wait.side_effect = WaiterError(name="change_set_create_complete", reason="Failed", last_response={})
        client.describe_change_set.return_value = {"Status": "FAILED", "StatusReason": "The submitted information didn't contain changes."}
        environment = {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": DEPLOY.EXPECTED_REPOSITORY,
                       "GITHUB_REF": DEPLOY.EXPECTED_REF}
        with TemporaryDirectory() as directory, patch.object(DEPLOY, "ARTIFACT_DIRECTORY", Path(directory)), patch.dict(os.environ, environment, clear=True):
            DEPLOY.package_and_deploy(client, "example-stack", "EXAMPLE123", artifact_key="serverless/project/dev/lambda/test.zip")
        client.execute_change_set.assert_not_called()
        client.delete_change_set.assert_called_once_with(ChangeSetName="example-change-set")

    def test_replacement_change_set_is_not_executed(self):
        from unittest.mock import Mock
        client = Mock()
        client.describe_stacks.return_value = {"Stacks": [{}]}
        client.create_change_set.return_value = {"Id": "example-change-set"}
        client.get_paginator.return_value.paginate.return_value = [{"Changes": [{"ResourceChange": {
            "Action": "Modify", "LogicalResourceId": "KnowledgeBaseBucket", "ResourceType": "AWS::S3::Bucket", "Replacement": "Conditional"}}]}]
        environment = {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": DEPLOY.EXPECTED_REPOSITORY,
                       "GITHUB_REF": DEPLOY.EXPECTED_REF}
        with TemporaryDirectory() as directory, patch.object(DEPLOY, "ARTIFACT_DIRECTORY", Path(directory)), patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(RuntimeError, "migration"):
                DEPLOY.package_and_deploy(client, "example-stack", "*", artifact_key="serverless/project/dev/lambda/test.zip")
        client.execute_change_set.assert_not_called()

    def test_aws_access_errors_are_not_treated_as_missing_stack(self):
        from unittest.mock import Mock
        client = Mock()
        client.describe_stacks.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Denied"}}, "DescribeStacks")
        with self.assertRaises(ClientError):
            DEPLOY.stack_outputs(client, "example-stack")

    def test_other_knowledge_base_is_rejected_during_trust_verification(self):
        from unittest.mock import Mock
        iam = Mock()
        iam.get_role.return_value = {"Role": {"AssumeRolePolicyDocument": {"Statement": [{
            "Effect": "Allow", "Principal": {"Service": "bedrock.amazonaws.com"},
            "Condition": {"ArnLike": {"aws:SourceArn": "arn:aws:bedrock:us-east-1:123456789012:knowledge-base/*"}},
        }]}}}
        with patch.object(DEPLOY.boto3, "client", return_value=iam):
            with self.assertRaisesRegex(RuntimeError, "exact resource"):
                DEPLOY.verify({
                    "LearningKnowledgeBaseId": "EXAMPLE123",
                    "LearningKnowledgeBaseServiceRoleArn": "arn:aws:iam::123456789012:role/example",
                    "LearningKnowledgeBaseArn": "arn:aws:bedrock:us-east-1:123456789012:knowledge-base/EXAMPLE123",
                })


if __name__ == "__main__":
    unittest.main()
