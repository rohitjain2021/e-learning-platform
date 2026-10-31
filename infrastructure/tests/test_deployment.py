"""Check deployment safety without making AWS calls."""

import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from botocore.exceptions import ClientError


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "deploy.py"
SPEC = importlib.util.spec_from_file_location("project_deploy", SCRIPT_PATH)
DEPLOY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DEPLOY)


class DeploymentSafetyTests(unittest.TestCase):
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

    def test_missing_serverless_authentication_fails_before_aws_calls(self):
        with patch.dict(os.environ, {
            "GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": DEPLOY.EXPECTED_REPOSITORY,
            "GITHUB_REF": DEPLOY.EXPECTED_REF,
        }, clear=True):
            with self.assertRaisesRegex(RuntimeError, "Actions secrets"):
                DEPLOY.require_pipeline()

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
