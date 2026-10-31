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

    def test_native_deployment_is_also_blocked_locally(self):
        from unittest.mock import Mock
        client = Mock()
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(RuntimeError, "GitHub Actions"):
            DEPLOY.package_and_deploy(client, "example-stack", "*")
        client.create_change_set.assert_not_called()

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
            DEPLOY.package_and_deploy(client, "example-stack", "*")
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
            DEPLOY.package_and_deploy(client, "example-stack", "EXAMPLE123")
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
                DEPLOY.package_and_deploy(client, "example-stack", "*")
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
