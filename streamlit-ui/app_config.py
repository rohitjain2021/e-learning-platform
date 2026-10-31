"""Shared frontend settings. Environment variables override Streamlit secrets."""

import os
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlsplit


# Global settings and defaults.
APP_TITLE = "Learning Assistant"
APP_DESCRIPTION = "Ask a question about your learning documents."
MAX_QUESTION_LENGTH = 2000
DEFAULT_API_URL = ""
DEFAULT_API_BEARER_TOKEN = ""
DEFAULT_API_TIMEOUT_SECONDS = 30
CONNECT_TIMEOUT_SECONDS = 5
MAX_API_TIMEOUT_SECONDS = 120
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


@dataclass(frozen=True)
class Settings:
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
    values = secrets if secrets is not None else {}

    def value(name, default):
        return os.environ.get(name, values.get(name, default))

    api_url = str(value("API_URL", DEFAULT_API_URL)).strip()
    token = str(value("API_BEARER_TOKEN", DEFAULT_API_BEARER_TOKEN)).strip()
    try:
        timeout = int(value("API_TIMEOUT_SECONDS", DEFAULT_API_TIMEOUT_SECONDS))
        if not 1 <= timeout <= MAX_API_TIMEOUT_SECONDS:
            raise ValueError
    except (ValueError, TypeError) as error:
        raise ValueError("API_TIMEOUT_SECONDS must be between 1 and 120.") from error

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
