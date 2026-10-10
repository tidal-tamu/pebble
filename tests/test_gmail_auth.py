import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from gmail_auth import AuthorizationError, PROFILE_URL, save_token, verify_access


class GmailAuthorizationTests(unittest.TestCase):
    def test_token_save_creates_parent_and_replaces_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secrets" / "token.json"
            credentials = Mock()
            credentials.to_json.return_value = '{"refresh_token": "test-only"}'
            save_token(credentials, path)
            self.assertEqual(json.loads(path.read_text())["refresh_token"], "test-only")
            credentials.to_json.return_value = '{"refresh_token": "replacement"}'
            save_token(credentials, path)
            self.assertEqual(json.loads(path.read_text())["refresh_token"], "replacement")
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_profile_check_only_requests_metadata_with_timeout(self):
        with patch("gmail_auth.AuthorizedSession") as session_factory:
            session = session_factory.return_value.__enter__.return_value
            response = session.get.return_value
            response.status_code = 200
            response.json.return_value = {"emailAddress": "test@example.com"}
            self.assertEqual(verify_access(Mock())["emailAddress"], "test@example.com")
            session.get.assert_called_once_with(PROFILE_URL, timeout=(5, 20))

    def test_google_error_body_is_not_exposed(self):
        with patch("gmail_auth.AuthorizedSession") as session_factory:
            session = session_factory.return_value.__enter__.return_value
            response = session.get.return_value
            response.status_code = 403
            response.text = "private API response"
            with self.assertRaisesRegex(AuthorizationError, "HTTP 403") as error:
                verify_access(Mock())
            self.assertNotIn("private API response", str(error.exception))
