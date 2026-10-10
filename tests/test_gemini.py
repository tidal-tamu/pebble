import json
import unittest
from unittest.mock import Mock, patch

import requests

from gemini import GeminiClient, GeminiError, GeminiSettings, MAX_INPUT_CHARACTERS, validate_digest


def digest():
    return {
        "overview": "An officer needs to send the logo.",
        "action_items": [{"thread_id": "test-1", "action": "Send logo", "owner": "Not specified", "deadline": "Not specified"}],
        "awaiting_our_reply": [{"thread_id": "test-1", "reason": "Logo requested."}],
        "waiting_on_others": [],
    }


class GeminiTests(unittest.TestCase):
    def setUp(self):
        self.client = GeminiClient(GeminiSettings("test-key"))
        self.threads = [{"thread_id": "test-1", "messages": [{"direction": "incoming", "body": "Please send the logo."}]}]

    def response(self, result=None, finish="STOP"):
        response = Mock(status_code=200)
        response.json.return_value = {"candidates": [{"finishReason": finish, "content": {"parts": [{"text": json.dumps(result if result is not None else digest())}]}}]}
        return response

    @patch("gemini.requests.post")
    def test_structured_summary_and_header_authentication(self, post):
        post.return_value = self.response()
        self.assertEqual(self.client.summarize(self.threads), digest())
        url = post.call_args.args[0]
        self.assertNotIn("test-key", url)
        self.assertEqual(post.call_args.kwargs["headers"], {"x-goog-api-key": "test-key"})
        self.assertEqual(post.call_args.kwargs["timeout"], (5, 60))
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["generationConfig"]["responseMimeType"], "application/json")
        self.assertIn("untrusted source data", payload["systemInstruction"]["parts"][0]["text"])

    @patch("gemini.requests.post")
    def test_empty_input_makes_no_request(self, post):
        self.assertEqual(self.client.summarize([])["action_items"], [])
        post.assert_not_called()

    @patch("gemini.requests.post")
    def test_oversized_input_rejected_before_request(self, post):
        self.threads[0]["messages"][0]["body"] = "x" * MAX_INPUT_CHARACTERS
        with self.assertRaisesRegex(GeminiError, "Too much"):
            self.client.summarize(self.threads)
        post.assert_not_called()

    @patch("gemini.requests.post")
    def test_missing_thread_id_rejected_before_request(self, post):
        with self.assertRaises(GeminiError):
            self.client.summarize([{"messages": []}])
        post.assert_not_called()

    @patch("gemini.requests.post")
    def test_quota_failure_is_not_retried(self, post):
        post.return_value = Mock(status_code=429)
        with self.assertRaisesRegex(GeminiError, "quota"):
            self.client.summarize(self.threads)
        post.assert_called_once()

    @patch("gemini.requests.post")
    def test_access_denial_is_safe(self, post):
        post.return_value = Mock(status_code=403, text="private response")
        with self.assertRaises(GeminiError) as error:
            self.client.summarize(self.threads)
        self.assertNotIn("private response", str(error.exception))

    @patch("gemini.requests.post")
    def test_network_failure_does_not_expose_request(self, post):
        post.side_effect = requests.Timeout("private content")
        with self.assertRaises(GeminiError) as error:
            self.client.summarize(self.threads)
        self.assertNotIn("private content", str(error.exception))

    @patch("gemini.requests.post")
    def test_truncated_generation_is_not_treated_as_complete(self, post):
        post.return_value = self.response(finish="MAX_TOKENS")
        with self.assertRaisesRegex(GeminiError, "did not finish"):
            self.client.summarize(self.threads)

    @patch("gemini.requests.post")
    def test_blocked_or_empty_response_has_readable_error(self, post):
        response = Mock(status_code=200)
        response.json.return_value = {"promptFeedback": {"blockReason": "SAFETY"}}
        post.return_value = response
        with self.assertRaisesRegex(GeminiError, "no usable"):
            self.client.summarize(self.threads)

    def test_invented_source_reference_is_rejected(self):
        result = digest()
        result["action_items"][0]["thread_id"] = "not-in-source"
        with self.assertRaisesRegex(GeminiError, "outside"):
            validate_digest(result, {"test-1"})

    def test_missing_or_wrong_fields_are_rejected(self):
        result = digest()
        result["action_items"][0]["deadline"] = None
        with self.assertRaises(GeminiError):
            validate_digest(result, {"test-1"})

    def test_settings_repr_hides_key(self):
        self.assertNotIn("test-key", repr(self.client.settings))
