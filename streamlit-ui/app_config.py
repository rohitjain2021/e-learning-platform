"""Shared frontend settings. Environment variables override Streamlit secrets."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit


# Global settings and defaults.
# Application identity and input limits are shared so screens and request validation stay consistent.
APP_TITLE = "Learning Assistant"
APP_DESCRIPTION = "Ask a question about your learning documents."
MAX_QUESTION_LENGTH = 2000
# Connection defaults keep the app usable before setup; real credentials come from secrets or the environment.
DEFAULT_API_URL = ""
DEFAULT_API_BEARER_TOKEN = ""
DEFAULT_API_TIMEOUT_SECONDS = 30
CONNECT_TIMEOUT_SECONDS = 5
MAX_API_TIMEOUT_SECONDS = 120
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
# Find assets and demo documents relative to this file, regardless of where the app starts.
UI_DIRECTORY = Path(__file__).resolve().parent
ASSETS_DIRECTORY = UI_DIRECTORY / "assets"
DEMO_DOCUMENT_DIRECTORY = UI_DIRECTORY.parent / "data" / "demo" / "learning"
# Describe the sample library once so navigation and document readers stay aligned.
DOCUMENT_CATALOG = (
    ("01-program-overview.txt", "Program overview", "Your program, people, and first steps"),
    ("02-hours-and-submissions.txt", "Hours & submissions", "Tracking, deadlines, and requirements"),
    ("03-competencies-and-evidence.txt", "Competencies & evidence", "Evidence requirements and examples"),
    ("04-mentor-review-and-escalation.txt", "Mentor review guide", "How reviews work and what to expect"),
    ("05-progress-and-reporting.txt", "Progress & reporting", "Understand progress and next steps"),
)
# Give learners useful starting questions drawn from the sample content.
SUGGESTED_QUESTIONS = (
    "Do pending practice hours count toward completion?",
    "When should I submit my weekly hours?",
    "What evidence do I need for a competency review?",
)


@dataclass(frozen=True)
class Settings:
    """Keep validated connection settings together so the UI and API client use the same values."""
    api_url: str
    bearer_token: str
    timeout_seconds: int


def is_https_url(value: str) -> bool:
    """Allow browser links only to well-formed HTTPS locations."""
    try:
        parsed = urlsplit(value)
        return bool(
            parsed.scheme == "https"
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and not any(char.isspace() for char in value)
        )
    except ValueError:
        return False


def load_settings(secrets: Mapping | None = None) -> Settings:
    """Read environment overrides and Streamlit secrets, then validate the endpoint and timeout."""
    values = secrets if secrets is not None else {}

    def value(name, default):
        """Prefer an environment setting, then a Streamlit secret, then the shared default."""
        return os.environ.get(name, values.get(name, default))

    api_url = str(value("API_URL", DEFAULT_API_URL)).strip()
    token = str(value("API_BEARER_TOKEN", DEFAULT_API_BEARER_TOKEN)).strip()
    try:
        timeout = int(value("API_TIMEOUT_SECONDS", DEFAULT_API_TIMEOUT_SECONDS))
        if not 1 <= timeout <= MAX_API_TIMEOUT_SECONDS:
            raise ValueError
    except (ValueError, TypeError) as error:
        raise ValueError("API_TIMEOUT_SECONDS must be between 1 and 120.") from error

    # Require encrypted API connections, while allowing local HTTP during development.
    if api_url:
        try:
            parsed = urlsplit(api_url)
            local_http = (
                parsed.scheme == "http"
                and parsed.hostname in LOCAL_HOSTS
                and not parsed.username
                and not parsed.password
                and not any(char.isspace() for char in api_url)
            )
        except ValueError:
            local_http = False
        if not is_https_url(api_url) and not local_http:
            raise ValueError("API_URL must use HTTPS, or HTTP on localhost for development.")
    return Settings(api_url, token, timeout)
