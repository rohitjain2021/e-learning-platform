"""HTTP boundary between the Streamlit frontend and the question-answering API."""

from dataclasses import dataclass

import requests

from app_config import CONNECT_TIMEOUT_SECONDS, MAX_QUESTION_LENGTH, Settings, is_https_url


# Shared user-facing messages; never display raw backend errors or credentials.
CONNECTION_ERROR = "We couldn't reach the assistant. Please try again shortly."
TIMEOUT_ERROR = "The answer took too long. Please try again."
RESPONSE_ERROR = "The assistant returned an unexpected response. Please try again."
STATUS_ERRORS = {
    401: "The assistant couldn't authorize this request. Please contact the administrator.",
    403: "The assistant couldn't authorize this request. Please contact the administrator.",
    429: "The assistant is busy. Please wait a moment and try again.",
}


class AssistantError(Exception):
    """An error safe to display to the user."""


@dataclass(frozen=True)
class Source:
    title: str
    url: str
    uri: str


@dataclass(frozen=True)
class Answer:
    text: str
    sources: tuple[Source, ...]


def ask_question(question: str, settings: Settings) -> Answer:
    question = question.strip()
    if not question:
        raise AssistantError("Please enter a question.")
    if len(question) > MAX_QUESTION_LENGTH:
        raise AssistantError(f"Please keep your question under {MAX_QUESTION_LENGTH} characters.")
    if not settings.api_url:
        raise AssistantError("The assistant is not connected yet. Please try again later.")

    headers = {"Accept": "application/json"}
    if settings.bearer_token:
        headers["Authorization"] = f"Bearer {settings.bearer_token}"
    try:
        response = requests.post(
            settings.api_url,
            json={"prompt": question},
            headers=headers,
            timeout=(CONNECT_TIMEOUT_SECONDS, settings.timeout_seconds),
            allow_redirects=False,
        )
    except requests.Timeout as error:
        raise AssistantError(TIMEOUT_ERROR) from error
    except requests.RequestException as error:
        raise AssistantError(CONNECTION_ERROR) from error

    if response.status_code != 200:
        raise AssistantError(STATUS_ERRORS.get(response.status_code, CONNECTION_ERROR))

    try:
        payload = response.json()
    except ValueError as error:
        raise AssistantError(RESPONSE_ERROR) from error
    if not isinstance(payload, dict):
        raise AssistantError(RESPONSE_ERROR)
    text = payload.get("answer")
    sources = payload.get("sources", [])
    if not isinstance(text, str) or not text.strip() or not isinstance(sources, list):
        raise AssistantError(RESPONSE_ERROR)

    parsed_sources = []
    for index, source in enumerate(sources, start=1):
        if not isinstance(source, dict):
            raise AssistantError(RESPONSE_ERROR)
        title = source.get("title") or f"Source {index}"
        url = source.get("url", "")
        uri = source.get("uri", "")
        if not all(isinstance(item, str) for item in (title, url, uri)):
            raise AssistantError(RESPONSE_ERROR)
        # An S3 URI identifies a private object; it is not a public browser link.
        parsed_sources.append(Source(title, url if is_https_url(url) else "", uri))
    return Answer(text.strip(), tuple(parsed_sources))
