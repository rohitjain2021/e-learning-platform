"""Frontend behavior checks use mocked HTTP; no AWS or model calls are made."""

import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from streamlit.testing.v1 import AppTest

from api_client import AssistantError, ask_question
from app_config import Settings, load_settings


# Test fixtures, never real endpoints or credentials.
TEST_API_URL = "https://api.example.com/ask"
TEST_SOURCE_URL = "https://documents.example.com/hours.txt"
TEST_SETTINGS = Settings(TEST_API_URL, "test-only-token", 30)
TEST_QUESTION = "Do pending hours count?"
TEST_ANSWER = "Only approved hours count toward completion."
APP_PATH = str(Path(__file__).resolve().parents[1] / "streamlit_app.py")


def response(payload, status=200):
    result = Mock(status_code=status)
    result.json.return_value = payload
    return result


class ApiClientTests(unittest.TestCase):
    def test_request_and_source_contract(self):
        with patch("api_client.requests.post", return_value=response({
            "answer": TEST_ANSWER,
            "sources": [{"title": "Hours guide", "url": TEST_SOURCE_URL}],
        })) as post:
            answer = ask_question("  " + TEST_QUESTION + "  ", TEST_SETTINGS)
        self.assertEqual(answer.text, TEST_ANSWER)
        self.assertEqual(answer.sources[0].url, TEST_SOURCE_URL)
        self.assertEqual(post.call_args.kwargs["json"], {"prompt": TEST_QUESTION})
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer test-only-token")
        self.assertFalse(post.call_args.kwargs["allow_redirects"])

    def test_empty_question_does_not_call_api(self):
        with patch("api_client.requests.post") as post:
            with self.assertRaisesRegex(AssistantError, "enter a question"):
                ask_question("   ", TEST_SETTINGS)
        post.assert_not_called()

    def test_private_and_unsafe_locations_are_not_clickable(self):
        for url in ("s3://private-bucket/hours.txt", "javascript:alert(1)", "http://example.com"):
            with self.subTest(url=url), patch("api_client.requests.post", return_value=response({
                "answer": TEST_ANSWER,
                "sources": [{"title": "Guide", "url": url, "uri": "s3://private-bucket/hours.txt"}],
            })):
                answer = ask_question(TEST_QUESTION, TEST_SETTINGS)
                self.assertEqual(answer.sources[0].url, "")
                self.assertTrue(answer.sources[0].uri.startswith("s3://"))

    def test_errors_do_not_reveal_backend_contents(self):
        for status in (302, 401, 403, 429, 500):
            with self.subTest(status=status), patch("api_client.requests.post", return_value=response({
                "error": "private backend details",
            }, status)):
                with self.assertRaises(AssistantError) as captured:
                    ask_question(TEST_QUESTION, TEST_SETTINGS)
                self.assertNotIn("private backend details", str(captured.exception))

    def test_malformed_success_is_rejected(self):
        for payload in ([], {}, {"answer": ""}, {"answer": TEST_ANSWER, "sources": [42]}):
            with self.subTest(payload=payload), patch("api_client.requests.post", return_value=response(payload)):
                with self.assertRaisesRegex(AssistantError, "unexpected response"):
                    ask_question(TEST_QUESTION, TEST_SETTINGS)

    def test_environment_overrides_secrets(self):
        with patch.dict(os.environ, {"API_URL": TEST_API_URL, "API_TIMEOUT_SECONDS": "45", "API_BEARER_TOKEN": ""}):
            settings = load_settings({"API_URL": "https://other.example.com", "API_TIMEOUT_SECONDS": 10})
        self.assertEqual(settings.api_url, TEST_API_URL)
        self.assertEqual(settings.timeout_seconds, 45)

    def test_invalid_configuration_is_rejected(self):
        with patch.dict(os.environ, {"API_URL": "http://remote.example.com", "API_TIMEOUT_SECONDS": "30"}):
            with self.assertRaises(ValueError):
                load_settings()


class StreamlitPageTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "API_URL": TEST_API_URL,
            "API_TIMEOUT_SECONDS": "30",
            "API_BEARER_TOKEN": "",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_disconnected_page_has_no_fabricated_answer(self):
        with patch.dict(os.environ, {"API_URL": ""}):
            app = AppTest.from_file(APP_PATH).run()
        self.assertFalse(app.exception)
        self.assertTrue(app.button[0].disabled)
        self.assertTrue(app.info)
        self.assertFalse(app.subheader)

    def test_answer_and_source_are_rendered(self):
        app = AppTest.from_file(APP_PATH).run()
        with patch("api_client.requests.post", return_value=response({
            "answer": TEST_ANSWER,
            "sources": [{"title": "Hours guide", "url": TEST_SOURCE_URL}],
        })):
            app.text_area[0].set_value(TEST_QUESTION)
            app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertIn(TEST_ANSWER, [item.value for item in app.text])
        self.assertEqual(app.get("link_button")[0].proto.url, TEST_SOURCE_URL)

    def test_empty_submission_shows_validation_error(self):
        app = AppTest.from_file(APP_PATH).run()
        with patch("api_client.requests.post") as post:
            app.button[0].click().run()
        post.assert_not_called()
        self.assertIn("enter a question", app.error[0].value)
        self.assertFalse(app.exception)

    def test_timeout_clears_previous_answer(self):
        app = AppTest.from_file(APP_PATH).run()
        with patch("api_client.requests.post", return_value=response({"answer": TEST_ANSWER})):
            app.text_area[0].set_value(TEST_QUESTION)
            app.button[0].click().run()
        self.assertIn(TEST_ANSWER, [item.value for item in app.text])
        with patch("api_client.requests.post", side_effect=requests.Timeout):
            app.text_area[0].set_value("What evidence is required?")
            app.button[0].click().run()
        self.assertIn("too long", app.error[0].value)
        self.assertNotIn(TEST_ANSWER, [item.value for item in app.text])
        self.assertFalse(app.exception)

    def test_suggestion_fills_question_without_calling_api(self):
        app = AppTest.from_file(APP_PATH).run()
        with patch("api_client.requests.post") as post:
            app.button(key="suggestion_0").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.text_area[0].value, "Do pending practice hours count toward completion?")
        post.assert_not_called()

    def test_document_library_navigation_and_reader(self):
        app = AppTest.from_file(APP_PATH).run()
        app.radio[0].set_value("Documents").run()
        self.assertFalse(app.exception)
        self.assertFalse(app.text_area)
        app.button(key="read_document_0").click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any("PROGRAM FACTS" in item.value for item in app.text))

    def test_suggested_question_can_be_submitted_without_typing_again(self):
        """The question visible after choosing a suggestion must be the one sent to the API."""
        app = AppTest.from_file(APP_PATH).run()
        app.button(key="suggestion_1").click().run()
        self.assertEqual(app.text_area[0].value, "When should I submit my weekly hours?")
        self.assertFalse(app.button(key="find_answer").disabled)
        with patch("api_client.requests.post", return_value=response({"answer": TEST_ANSWER})) as post:
            app.button(key="find_answer").click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        self.assertEqual(post.call_args.kwargs["json"], {"prompt": "When should I submit my weekly hours?"})


if __name__ == "__main__":
    unittest.main()
