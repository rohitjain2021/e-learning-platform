"""Exercise the reference Lambda's retrieval flow without making AWS calls."""

import base64
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from botocore.exceptions import ReadTimeoutError
from botocore.session import Session
from botocore.validate import validate_parameters


# Load the exact source packaged by CI; the SDK clients are replaced with test doubles.
SOURCE_PATH = Path(__file__).resolve().parents[1] / "src" / "lambda.py"
SPEC = importlib.util.spec_from_file_location("learning_lambda", SOURCE_PATH)
APP = importlib.util.module_from_spec(SPEC)
with patch("boto3.client", return_value=Mock()):
    SPEC.loader.exec_module(APP)
TEST_MODEL_ARN = "arn:aws:bedrock:us-east-1:123456789012:inference-profile/us.amazon.nova-pro-v1:0"
TEST_URI = "s3://e-learning-platform-knowledge-base/demo/learning/program.txt"
TEST_ANSWER = "Submit your hours by Friday."


class Stream(list):
    """Model an SDK event stream and record whether the handler releases its connection."""

    closed = False

    def close(self):
        """Record stream cleanup on both successful and failed requests."""
        self.closed = True


class LambdaRetrievalTests(unittest.TestCase):
    """Check the reference response fields, failures, privacy, and frontend source links."""

    def setUp(self):
        """Supply project configuration and simulated SDK clients for each isolated request."""
        self.client = Mock()
        self.signer = Mock()
        self.signer.generate_presigned_url.return_value = "https://example.com/temporary-document"
        for name, value in {"KNOWLEDGE_BASE_ID": "EXAMPLE1234", "ANSWER_MODEL_ARN": TEST_MODEL_ARN,
                            "SOURCE_BUCKET": "e-learning-platform-knowledge-base", "SOURCE_PREFIX": "demo/learning/"}.items():
            cleanup = patch.object(APP, name, value)
            cleanup.start()
            self.addCleanup(cleanup.stop)
        for name, client in (("BEDROCK", self.client), ("S3", self.signer)):
            cleanup = patch.object(APP, name, client)
            cleanup.start()
            self.addCleanup(cleanup.stop)

    def response_stream(self, **overrides):
        """Build realistic trace, partial-text, and final-result events with source metadata."""
        result = {"generatedResponse": {"answer": TEST_ANSWER, "citations": [
            {"startIndex": 0, "endIndex": 10, "references": [{"resultIndex": 0}]}]},
            "results": [{"content": {"text": "Private supporting passage"},
                         "metadata": {"x-amz-bedrock-kb-source-uri": TEST_URI},
                         "sourceRetriever": {"identifier": "EXAMPLE1234"}}]}
        result.update(overrides)
        stream = Stream([{"traceEvent": {"attributes": {"message": "Private reasoning"}}},
                         {"responseEvent": {"text": "Partial answer"}}, {"result": result}])
        self.client.agentic_retrieve_stream.return_value = {"stream": stream}
        return stream, result

    def test_reference_flow_preserves_answer_citations_results_and_closes_stream(self):
        """Read prompt, invoke the selected API, and preserve every original response field."""
        stream, result = self.response_stream()
        response = APP.lambda_handler({"prompt": " When should I submit hours? "}, None)
        self.assertEqual(response["statusCode"], 200)
        body = json.loads(response["body"])
        self.assertEqual(body["answer"], result["generatedResponse"]["answer"])
        self.assertEqual(body["citations"], result["generatedResponse"]["citations"])
        self.assertEqual(body["results"], result["results"])
        self.assertEqual(body["sources"][0]["uri"], TEST_URI)
        self.assertTrue(stream.closed)
        request = self.client.agentic_retrieve_stream.call_args.kwargs
        shape = Session().get_service_model("bedrock-agent-runtime").operation_model("AgenticRetrieveStream").input_shape
        validate_parameters(request, shape)
        self.assertEqual(request["messages"][0]["content"]["text"], "When should I submit hours?")
        self.assertEqual(request["retrievers"][0]["configuration"]["knowledgeBase"]["knowledgeBaseId"], "EXAMPLE1234")
        self.assertTrue(request["generateResponse"])
        config = request["agenticRetrieveConfiguration"]
        self.assertEqual(config["foundationModelType"], "CUSTOM")
        self.assertEqual(config["rerankingModelType"], "NONE")
        self.assertEqual(config["foundationModelConfiguration"]["bedrockFoundationModelConfiguration"]["modelConfiguration"]["modelArn"], TEST_MODEL_ARN)

    def test_managed_metadata_returns_a_signed_document_link(self):
        """Use the managed S3 document ID instead of its private HTTPS source location."""
        result = {"content": {"text": "Only approved hours count."},
                  "metadata": {"_data_source_type": "S3", "_document_id": TEST_URI,
                               "_source_uri": "https://e-learning-platform-knowledge-base.s3.amazonaws.com/demo/learning/program.txt"},
                  "sourceRetriever": {"identifier": "EXAMPLE1234"}}
        self.response_stream(results=[result, result])
        response = APP.lambda_handler({"prompt": "Do pending hours count?"}, None)
        body = json.loads(response["body"])
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(body["results"], [result, result])
        self.assertEqual(body["sources"], [{"title": "program.txt", "uri": TEST_URI,
                                          "url": "https://example.com/temporary-document"}])
        self.signer.generate_presigned_url.assert_called_once_with(
            "get_object", Params={"Bucket": "e-learning-platform-knowledge-base",
                                  "Key": "demo/learning/program.txt"}, ExpiresIn=APP.SOURCE_LINK_EXPIRY_SECONDS)

    def test_api_gateway_json_and_base64_bodies_are_supported(self):
        """Accept the HTTP envelope as well as the reference's direct prompt event."""
        for encoded in (False, True):
            with self.subTest(encoded=encoded):
                self.response_stream()
                body = json.dumps({"prompt": "When should I submit hours?"})
                if encoded:
                    body = base64.b64encode(body.encode()).decode()
                response = APP.lambda_handler({"body": body, "isBase64Encoded": encoded}, None)
                self.assertEqual(response["statusCode"], 200)

    def test_invalid_questions_do_not_call_bedrock(self):
        """Reject missing, malformed, non-text, and oversized questions before model invocation."""
        for event in (None, {}, {"prompt": " "}, {"prompt": 123}, {"prompt": "x" * 2001},
                      {"body": "not-json"}, {"body": "[]"}, {"body": "!", "isBase64Encoded": True}):
            with self.subTest(event=event):
                self.assertEqual(APP.lambda_handler(event, None)["statusCode"], 400)
        self.client.agentic_retrieve_stream.assert_not_called()

    def test_missing_configuration_returns_a_safe_error(self):
        """An unset model or knowledge base must not result in an unintended AWS request."""
        with patch.object(APP, "ANSWER_MODEL_ARN", ""):
            self.assertEqual(APP.lambda_handler({"prompt": "Question"}, None)["statusCode"], 503)
        self.client.agentic_retrieve_stream.assert_not_called()

    def test_in_band_errors_discard_partial_answers(self):
        """A failed stream must never be reported as a successful partial answer."""
        for error, status in (("throttlingException", 429), ("accessDeniedException", 503),
                              ("internalServerException", 502), ("validationException", 502)):
            with self.subTest(error=error):
                stream = Stream([{"responseEvent": {"text": "private partial answer"}},
                                 {error: {"message": "private AWS details"}}])
                self.client.agentic_retrieve_stream.return_value = {"stream": stream}
                response = APP.lambda_handler({"prompt": "Question"}, None)
                self.assertEqual(response["statusCode"], status)
                self.assertNotIn("private", response["body"])
                self.assertTrue(stream.closed)

    def test_missing_or_incomplete_final_result_is_not_a_success(self):
        """Keep the answer, citations, and results contract intact if Bedrock ends unexpectedly."""
        for chunks in ([], [{"responseEvent": {"text": "partial"}}], [{"result": {}}],
                       [{"result": {"generatedResponse": {"answer": ""}, "results": []}}]):
            with self.subTest(chunks=chunks):
                stream = Stream(chunks)
                self.client.agentic_retrieve_stream.return_value = {"stream": stream}
                self.assertEqual(APP.lambda_handler({"prompt": "Question"}, None)["statusCode"], 502)
                self.assertTrue(stream.closed)

    def test_optional_citations_and_results_default_to_empty_lists(self):
        """Preserve the reference function's behavior when optional evidence is absent."""
        self.response_stream(generatedResponse={"answer": "No matching guidance was found."}, results=[])
        body = json.loads(APP.lambda_handler({"prompt": "Question"}, None)["body"])
        self.assertEqual(body["citations"], [])
        self.assertEqual(body["results"], [])
        self.assertEqual(body["sources"], [])

    def test_source_links_are_signed_only_for_the_project_document_prefix(self):
        """Do not grant access to unrelated S3 objects named in returned metadata."""
        uris = [TEST_URI, TEST_URI, "s3://another-bucket/demo/learning/private.txt",
                "s3://e-learning-platform-knowledge-base/private/secret.txt",
                "s3://e-learning-platform-knowledge-base/demo/learning/../private.txt"]
        results = [{"metadata": {"x-amz-bedrock-kb-source-uri": uri}} for uri in uris]
        sources = APP.source_links(results, [{"references": [{"resultIndex": i} for i in range(len(results))]}])
        self.assertEqual(len(sources), 4)
        self.signer.generate_presigned_url.assert_called_once_with(
            "get_object", Params={"Bucket": "e-learning-platform-knowledge-base", "Key": "demo/learning/program.txt"},
            ExpiresIn=APP.SOURCE_LINK_EXPIRY_SECONDS)
        self.assertTrue(sources[0]["url"].startswith("https://"))
        self.assertTrue(all(not source["url"] for source in sources[1:]))

    def test_only_cited_documents_are_linked_and_duplicate_chunks_share_one_link(self):
        """Searching several documents must not present all of them as answer evidence."""
        cited_uri = TEST_URI.replace("program.txt", "hours.txt")
        results = [{"metadata": {"_document_id": uri}}
                   for uri in [TEST_URI, cited_uri, cited_uri, TEST_URI.replace("program.txt", "evidence.txt")]]
        citations = [{"references": [{"resultIndex": 1}, {"resultIndex": 2}, {"resultIndex": 1}]}]
        self.response_stream(results=results, generatedResponse={"answer": TEST_ANSWER, "citations": citations})
        body = json.loads(APP.lambda_handler({"prompt": "When are hours due?"}, None)["body"])
        self.assertEqual([s["uri"] for s in body["sources"]], [cited_uri])
        self.assertEqual(body["results"], results)
        self.assertEqual(body["citations"], citations)
        self.assertEqual(self.signer.generate_presigned_url.call_count, 1)

    def test_missing_or_invalid_citations_never_link_uncited_search_results(self):
        """Missing evidence must not fall back to every retrieved document."""
        results = [{"metadata": {"_document_id": TEST_URI}}]
        for citations in ([], [{"references": [{}, {"resultIndex": -1}, {"resultIndex": 99},
                                               {"resultIndex": "0"}, {"resultIndex": True}]}]):
            with self.subTest(citations=citations):
                self.assertEqual(APP.source_links(results, citations), [])
        self.signer.generate_presigned_url.assert_not_called()

    def test_request_and_document_contents_are_not_logged(self):
        """Log useful counts without writing questions, answers, or document text to CloudWatch."""
        self.response_stream()
        with self.assertLogs(APP.LOGGER, level="INFO") as captured:
            APP.lambda_handler({"prompt": "Private user question"}, None)
        output = " ".join(captured.output)
        for sensitive in ("Private user question", TEST_ANSWER, "Private supporting passage", "Private reasoning", TEST_URI):
            self.assertNotIn(sensitive, output)

    def test_timeouts_and_approaching_lambda_deadline_return_safe_errors(self):
        """Stop cleanly on SDK read timeouts or when Lambda is about to run out of time."""
        self.client.agentic_retrieve_stream.side_effect = ReadTimeoutError(endpoint_url="https://private-endpoint")
        response = APP.lambda_handler({"prompt": "Question"}, None)
        self.assertEqual(response["statusCode"], 504)
        self.assertNotIn("private-endpoint", response["body"])
        self.client.agentic_retrieve_stream.side_effect = None
        stream, _ = self.response_stream()
        context = Mock()
        context.get_remaining_time_in_millis.return_value = 1000
        self.assertEqual(APP.lambda_handler({"prompt": "Question"}, context)["statusCode"], 504)
        self.assertTrue(stream.closed)

    def test_health_request_never_invokes_bedrock(self):
        """Allow CI/CD to verify the deployment without asking a real question or incurring inference cost."""
        response = APP.lambda_handler({"action": "health"}, None)
        self.assertEqual(json.loads(response["body"])["status"], "ready")
        self.client.agentic_retrieve_stream.assert_not_called()

    def test_lambda_filename_can_be_imported_by_the_runtime(self):
        """The AWS runtime loads lambda.py by module name even though lambda is a Python keyword."""
        import importlib
        import sys
        with patch("boto3.client", return_value=Mock()), patch.object(sys, "path", [str(SOURCE_PATH.parent), *sys.path]):
            module = importlib.import_module("lambda")
            self.assertTrue(callable(module.lambda_handler))
        sys.modules.pop("lambda", None)


if __name__ == "__main__":
    unittest.main()
