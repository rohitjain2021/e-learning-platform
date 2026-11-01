# Backend

The function is `E-learning`, runs on Python 3.14, and has a **120-second (two-minute) execution limit**. Its code is `src/lambda.py`, and its AWS handler setting is `lambda.lambda_handler`.

## How to read the code

The code follows the original `RAG_eLearning_App_v1.0/RAG_eLearning_App/lambda_function.py` in six numbered steps:

1. Define the settings at the top of the file.
2. Create the Bedrock and S3 connections once.
3. Read the user's `prompt` and check that it is valid.
4. Call `agentic_retrieve_stream` using Nova Pro and our knowledge base.
5. Wait for the final answer, citations, and retrieved passages.
6. Return those values as JSON, together with source links for Streamlit.

There are no custom exception classes, client-cache decorators, or request-time AWS resource lookups. The two small helpers format JSON responses and build source links. The handler contains the request flow and normal error handling. It accepts both `{"prompt":"..."}` and API Gateway JSON bodies. `{"action":"health"}` checks deployment without invoking a model.

## Where the AWS values come from

Local AWS checks use the configured credential profile. The project's CloudFormation stack supplies `KNOWLEDGE_BASE_ID` through its knowledge-base resource reference. It builds `ANSWER_MODEL_ARN` from the deployed account, region, and Nova Pro inference-profile ID. Lambda reads those environment variables at the top of the file and uses its execution role for AWS authentication. The other example account and knowledge-base ID are not copied, and a local AWS profile is not packaged into Lambda.

The inputs live at the top of `infrastructure/resources/lambda.yml`, including the 120-second execution limit. Source links last 300 seconds by default. Only documents in the configured bucket and prefix receive signed links; unrelated locations and traversal paths do not. The runtime role is restricted to the project knowledge base, the selected model profile, source documents, and its own logs. Questions, answers, and document contents are not printed to logs.

## Tests and deployment

Install `backend/requirements.txt`, then run `python -m unittest discover -s tests -v` from this directory. Tests mock AWS clients. CI also runs them on Python 3.14 and packages `lambda.py` with the pinned AWS SDK.

The function must be deployed through GitHub Actions and CloudFormation. The scoped Lambda permissions in the bootstrap template must be applied through the approved process before the first deployment. Documents must be synchronized before querying them. API Gateway and Streamlit endpoint setup remain separate work. Set Streamlit's `API_TIMEOUT_SECONDS` appropriately for agentic requests; its current default is 30 seconds and maximum is 120 seconds.
