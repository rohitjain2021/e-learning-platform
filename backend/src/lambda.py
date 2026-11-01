"""Ask the project knowledge base a question and return its answer and sources."""

import base64
import json
import logging
import os
from urllib.parse import unquote, urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, ConnectTimeoutError, ReadTimeoutError


# 1. Settings: CloudFormation supplies the project knowledge base, model, and document folder.
SERVICE_NAME = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "E-learning")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
KNOWLEDGE_BASE_ID = os.environ.get("KNOWLEDGE_BASE_ID", "")
ANSWER_MODEL_ARN = os.environ.get("ANSWER_MODEL_ARN", "")
SOURCE_BUCKET = os.environ.get("SOURCE_BUCKET", "")
SOURCE_PREFIX = os.environ.get("SOURCE_PREFIX", "demo/learning/")
MAX_QUESTION_LENGTH = 2000
MAX_AGENT_ITERATIONS = int(os.environ.get("MAX_AGENT_ITERATIONS", "3"))
SOURCE_LINK_EXPIRY_SECONDS = int(os.environ.get("SOURCE_LINK_EXPIRY_SECONDS", "300"))
READ_TIMEOUT_SECONDS = 110  # Leave time to return an error before Lambda's 120-second limit.
DEADLINE_MARGIN_MILLISECONDS = 5000
RESPONSE_HEADERS = {"Content-Type": "application/json", "Cache-Control": "no-store"}
LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)

# 2. AWS connections: Lambda uses its IAM role; local runs use the configured AWS profile.
BEDROCK = boto3.client("bedrock-agent-runtime", region_name=AWS_REGION,
                      config=Config(read_timeout=READ_TIMEOUT_SECONDS, connect_timeout=5,
                                    retries={"total_max_attempts": 1}))
S3 = boto3.client("s3", region_name=AWS_REGION, config=Config(signature_version="s3v4"))


def json_response(status_code, body):
    """Put a JSON result in the response format expected by API Gateway."""
    return {"statusCode": status_code, "headers": dict(RESPONSE_HEADERS),
            "body": json.dumps(body, default=str)}


def source_links(results):
    """Create short-lived links only for documents in this project's S3 folder."""
    sources = []
    seen = set()
    for result in results:
        metadata = result.get("metadata", {})
        # Managed knowledge bases put the S3 URI in _document_id.
        # Keep the older metadata key working for the reference response format too.
        uri = metadata.get("x-amz-bedrock-kb-source-uri") or metadata.get("_document_id", "")
        if not isinstance(uri, str) or not uri or uri in seen:
            continue
        seen.add(uri)
        try:
            location = urlsplit(uri)
        except ValueError:
            continue
        key = unquote(location.path.lstrip("/"))
        url = ""
        # Do not give access to another bucket, another folder, or a path containing '..'.
        if (location.scheme == "s3" and location.netloc == SOURCE_BUCKET and SOURCE_BUCKET
                and key.startswith(SOURCE_PREFIX) and not location.query and not location.fragment
                and not any(part in (".", "..") for part in key.split("/"))):
            url = S3.generate_presigned_url("get_object",
                    Params={"Bucket": SOURCE_BUCKET, "Key": key}, ExpiresIn=SOURCE_LINK_EXPIRY_SECONDS)
        sources.append({"title": key.rsplit("/", 1)[-1] or "Source", "uri": uri, "url": url})
    return sources


def lambda_handler(event, context):
    """Read the question, ask Bedrock, and return the complete answer with its evidence."""
    # A health check lets the pipeline test deployment without calling an AI model.
    if not isinstance(event, dict):
        return json_response(400, {"error": "Send a JSON object containing a prompt."})
    if event.get("action") == "health":
        return json_response(200, {"service": SERVICE_NAME, "status": "ready"})

    # 3. Read the question, either directly or from an API Gateway request body.
    try:
        payload = event
        if "body" in event:
            body = event["body"]
            if event.get("isBase64Encoded"):
                body = base64.b64decode(body, validate=True)
            payload = json.loads(body)
    except (ValueError, TypeError, UnicodeDecodeError):
        return json_response(400, {"error": "The request body must contain valid JSON."})
    query = payload.get("prompt") if isinstance(payload, dict) else None
    if not isinstance(query, str) or not query.strip():
        return json_response(400, {"error": "Please enter a question in the prompt field."})
    query = query.strip()
    if len(query) > MAX_QUESTION_LENGTH:
        return json_response(400, {"error": f"Please keep your question under {MAX_QUESTION_LENGTH} characters."})
    if not KNOWLEDGE_BASE_ID or not ANSWER_MODEL_ARN:
        return json_response(503, {"error": "The assistant needs its knowledge base and model settings."})

    stream = None
    try:
        # 4. Ask Nova Pro to search our knowledge base and generate the answer.
        response = BEDROCK.agentic_retrieve_stream(
            agenticRetrieveConfiguration={
                "foundationModelType": "CUSTOM",
                "foundationModelConfiguration": {
                    "type": "BEDROCK_FOUNDATION_MODEL",
                    "bedrockFoundationModelConfiguration": {
                        "modelConfiguration": {"modelArn": ANSWER_MODEL_ARN}}},
                "rerankingModelType": "NONE",
                "maxAgentIteration": MAX_AGENT_ITERATIONS},
            generateResponse=True,
            messages=[{"role": "user", "content": {"text": query}}],
            retrievers=[{"configuration": {"knowledgeBase": {"knowledgeBaseId": KNOWLEDGE_BASE_ID}}}],
        )

        # 5. Ignore partial text; wait for the final answer, citations, and retrieved passages.
        stream = response["stream"]
        for chunk in stream:
            if context and context.get_remaining_time_in_millis() < DEADLINE_MARGIN_MILLISECONDS:
                return json_response(504, {"error": "The answer took too long. Please try again."})
            if "throttlingException" in chunk:
                return json_response(429, {"error": "The assistant is busy. Please try again shortly."})
            if "accessDeniedException" in chunk or "resourceNotFoundException" in chunk:
                return json_response(503, {"error": "The assistant needs an AWS access or knowledge-base configuration update."})
            if any(name.endswith("Exception") for name in chunk):
                return json_response(502, {"error": "The assistant could not finish the answer."})
            if "result" not in chunk:
                continue
            result = chunk["result"]
            generated = result.get("generatedResponse", {})
            answer = generated.get("answer")
            citations = generated.get("citations", [])
            results = result.get("results", [])
            if (not isinstance(answer, str) or not answer.strip()
                    or not isinstance(citations, list) or not isinstance(results, list)):
                return json_response(502, {"error": "The assistant returned an incomplete answer."})

            # 6. Return the reference response fields, plus source links for our Streamlit page.
            LOGGER.info("Answer completed: %d passages, %d citations", len(results), len(citations))
            return json_response(200, {"answer": answer, "citations": citations,
                                      "results": results, "sources": source_links(results)})
        return json_response(502, {"error": "The assistant did not return a complete answer."})
    except (ConnectTimeoutError, ReadTimeoutError):
        return json_response(504, {"error": "The answer took too long. Please try again."})
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "")
        # Record AWS's error code; access-denial messages identify the missing IAM permission.
        LOGGER.error("Bedrock request failed: %s", code)
        if code == "AccessDeniedException":
            LOGGER.error("AWS permission denial: %s", error.response["Error"].get("Message", ""))
        if code in ("ThrottlingException", "TooManyRequestsException"):
            return json_response(429, {"error": "The assistant is busy. Please try again shortly."})
        return json_response(503, {"error": "The assistant is temporarily unavailable."})
    except (BotoCoreError, KeyError, ValueError, TypeError, AttributeError):
        return json_response(502, {"error": "The assistant could not finish the answer."})
    finally:
        # Release the connection even when Bedrock fails or Lambda is about to time out.
        if stream is not None:
            stream.close()
