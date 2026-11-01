# Streamlit setup

The frontend has a question box, an Ask button, a loading indicator, an answer area, source links, and user-friendly errors. It calls the backend from the Streamlit server, not directly from the user's browser.

## Run locally

Use Python 3.12 from the repository root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m streamlit run streamlit-ui/streamlit_app.py
```

Open the local address printed by Streamlit. The page opens without API configuration, but Ask stays disabled until an endpoint is configured. The frontend does not return invented demo answers.

## Configure the connection

Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml`:

```powershell
Copy-Item .streamlit/secrets.toml.example .streamlit/secrets.toml
```

Set these values in the local secrets file, or in your hosting service's secrets settings:

```toml
API_URL = "https://ek0pkuy1wd.execute-api.us-east-1.amazonaws.com/dev/e-learning"
API_TIMEOUT_SECONDS = 30
API_BEARER_TOKEN = ""
```

This is the project's deployed question endpoint. If the API is recreated, use the new CloudFormation output `LearningApiUrl`, including its stage and resource path. Local environment variables are not copied to Streamlit Community Cloud: configure `API_URL` in the hosted app's Settings > Secrets as well. Without it, Find answer stays disabled even after selecting a suggested question.

Environment variables with the same names take precedence over the secrets file. Global application defaults and validation are in `streamlit-ui/app_config.py`. The timeout setting accepts 1 through 120 seconds. HTTP is allowed only on localhost for local backend development; hosted endpoints use HTTPS.

The UI code, tests, and frontend requirements are grouped in `streamlit-ui`. Shared `.streamlit` configuration stays at the repository root. Run Streamlit from the repository root to use the same configuration and secrets paths locally and on Community Cloud. The root requirements file includes the frontend requirements plus the AWS SDK for combined local development.

Leave the bearer token empty unless the API explicitly supports a server-to-server `Authorization: Bearer ...` token. This setting does not implement user sign-in, per-user permissions, AWS IAM request signing, or an API Gateway API key. Match authentication to the backend when that is implemented. Do not enter AWS access keys here or commit the real secrets file; it is already ignored by Git.

## Backend response contract

The frontend sends a POST request:

```json
{"prompt": "Do pending practice hours count toward completion?"}
```

The backend should return HTTP 200 with JSON such as:

```json
{
  "answer": "Only approved hours count toward completion.",
  "sources": [
    {
      "title": "Hours and submissions",
      "uri": "s3://e-learning-platform-knowledge-base/demo/learning/02-hours-and-submissions.txt",
      "url": "https://YOUR_AUTHORIZED_DOCUMENT_LINK"
    }
  ]
}
```

`answer` is required and must be nonempty text. `sources` is a list; each source can contain a title, an HTTPS URL, and a document URI. Missing source URLs are displayed as references without a clickable link. An empty source list produces a visible notice.

The backend must verify document access and supply a valid link, for example a short-lived S3 presigned URL for an authorized user. The frontend does not make the bucket public or generate signed URLs. Expired links need a new response with fresh URLs. Model text is displayed as plain text; clickable sources are rendered separately from the validated source URLs.

Timeouts, connection failures, denied requests, rate limits, and malformed responses produce friendly errors. Raw backend messages, credentials, and response bodies are not shown. Failed submissions clear the previous answer so users do not mistake it for the new response. Requests are not automatically retried, avoiding duplicate model calls.

## Host on Streamlit Community Cloud

After committing and pushing the frontend:

1. In Streamlit Community Cloud, create an app from your GitHub repository.
2. Select repository `rohitjain2021/e-learning-platform` and branch `main`.
3. Set the main file path to `streamlit-ui/streamlit_app.py` and Python to 3.12 in the deployment settings.
4. Paste the connection settings into the app's Secrets settings. The requirements file alongside the entry point supplies the frontend packages.
5. Save the hosted secrets, allow the app to restart, and test a question and its source link. Find answer becomes enabled when `API_URL` is configured.

Streamlit hosts the frontend. GitHub Actions checks the application and deploys AWS resources through CloudFormation; it does not provision Streamlit hosting or its connection settings. Community Cloud manages its own code updates from the connected branch. Supporting links come only from documents cited in the generated answer, with duplicate chunks grouped into one document link.

Official references: [secrets management](https://docs.streamlit.io/develop/concepts/connections/secrets-management), [Community Cloud deployment](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app).

## Run checks

```powershell
Push-Location streamlit-ui
python -m unittest discover -s tests -v
Pop-Location
```

The tests do not call AWS, the knowledge base, or a language model. They cover the disconnected page, question validation, answer/source rendering, HTTP errors, malformed responses, link handling, configuration, and clearing stale answers after a timeout.
