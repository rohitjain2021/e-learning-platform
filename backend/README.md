# Backend

The function is `E-learning`, runs on Python 3.14, and has a **120-second (two-minute) execution limit**. Its code is `src/lambda.py`, and its AWS handler setting is `lambda.lambda_handler`.

## How to read the code

The code follows the original `RAG_eLearning_App_v1.0/RAG_eLearning_App/lambda_function.py` in six numbered steps:

1. Define the settings at the top of the file.
2. Create the Bedrock and S3 connections once.
3. Read the user's `prompt` and check that it is valid.
4. Call `agentic_retrieve_stream` using Nova Pro and our knowledge base. Each search requests two relevant passages by default, keeping small queries focused. Configure this with `MaxRetrievalResults` at the top of the Lambda template.
5. Wait for the final answer, citations, and retrieved passages.
6. Return those values as JSON, together with links only to the documents cited in the answer. Multiple cited chunks from one document produce one link; uncited search results are not shown as supporting sources.

There are no custom exception classes, client-cache decorators, or request-time AWS resource lookups. The two small helpers format JSON responses and build source links. The handler contains the request flow and normal error handling. It accepts both `{"prompt":"..."}` and API Gateway JSON bodies. `{"action":"health"}` checks deployment without invoking a model.

`notify_question` is a small third helper. It counts each valid submission and sends the question, timestamp, and request ID to the configured SNS topic before the Bedrock call. It sends once per request, not for each response chunk. A publishing error is monitored and does not block the answer. Health checks and invalid questions do not send email. `NOTIFICATION_TOPIC_ARN` comes from CloudFormation; email delivery requires the recipient to confirm the SNS subscription.

## Where the AWS values come from

Local AWS checks use the configured credential profile. The project's CloudFormation stack supplies `KNOWLEDGE_BASE_ID` through its knowledge-base resource reference. It builds `ANSWER_MODEL_ARN` from the deployed account, region, and Nova Pro inference-profile ID. Lambda reads those environment variables at the top of the file and uses its execution role for AWS authentication. The other example account and knowledge-base ID are not copied, and a local AWS profile is not packaged into Lambda.

The inputs live at the top of `infrastructure/resources/lambda.yml`, including the 120-second execution limit. Source links last 300 seconds by default. Only documents in the configured bucket and prefix receive signed links; unrelated locations and traversal paths do not. The runtime role is restricted to the project knowledge base, the selected model profile, source documents, and its own logs. Questions, answers, and document contents are not printed to logs.

## Tests and deployment

Install `backend/requirements.txt`, then run `python -m unittest discover -s tests -v` from this directory. Tests mock AWS clients. CI also runs them on Python 3.14 and packages `lambda.py` with the pinned AWS SDK.

The function must be deployed through GitHub Actions and CloudFormation. The scoped deployment permissions in the bootstrap template must be applied through the approved process. Documents must be synchronized before querying them.

The regional REST API `e Learning` exposes `POST /e-learning` on the `dev` stage. Send `Content-Type: application/json` and `{"prompt":"When should I submit my weekly hours?"}`. Its custom request mapping passes that prompt directly to this handler; its response mapping unwraps `body` and preserves `statusCode`. Set Streamlit's `API_URL` to the CloudFormation output `LearningApiUrl`. The gateway's integration timeout is 29 seconds, separate from Lambda's 120-second limit; the frontend's current 30-second timeout covers that gateway wait.
