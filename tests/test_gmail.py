import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
import time
import unittest
from unittest.mock import MagicMock, Mock, patch

from gmail import GmailClient, GmailError, MAX_BODY, message_text, parse_thread


def part(text, mime="text/plain", **kwargs):
    return {"mimeType": mime, "body": {"data": base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")}, **kwargs}


def message(timestamp, text, labels=None, sender="sender@example.com"):
    payload = part(text)
    payload["headers"] = [{"name": "From", "value": sender}, {"name": "Subject", "value": "Test conversation"}]
    return {"internalDate": str(timestamp), "payload": payload, "labelIds": labels or []}


class GmailParsingTests(unittest.TestCase):
    def test_plain_text_is_preferred_and_attachments_are_excluded(self):
        payload = {"mimeType": "multipart/mixed", "parts": [
            {"mimeType": "multipart/alternative", "parts": [part("Please send a quote."), part("<p>Duplicate</p>", "text/html")]},
            part("Do not include attachment text", filename="notes.txt"),
        ]}
        self.assertEqual(message_text(payload), "Please send a quote.")

    def test_html_skips_scripts_and_quoted_history(self):
        payload = part('<p>Send &amp; confirm.</p><script>secret script</script><div class="gmail_quote">Old history</div>', "text/html")
        self.assertEqual(message_text(payload), "Send & confirm.")

    def test_empty_plain_part_falls_back_to_html(self):
        payload = {"mimeType": "multipart/alternative", "parts": [part(" "), part("<p>Visible request</p>", "text/html")]}
        self.assertEqual(message_text(payload), "Visible request")

    def test_plain_quoted_replies_are_removed(self):
        self.assertEqual(message_text(part("Yes, confirmed.\n\nOn Thursday, Alex wrote:\n> Need a reply")), "Yes, confirmed.")

    def test_messages_sorted_sent_labels_and_drafts(self):
        raw = {"id": "abc123", "messages": [message(3000, "Draft", ["DRAFT"]), message(2000, "Confirmed", ["SENT"]), message(1000, "Please confirm")]}
        parsed, shortened, unreadable = parse_thread(raw, "club@example.com")
        self.assertEqual([m["direction"] for m in parsed["messages"]], ["incoming", "outgoing"])
        self.assertEqual(parsed["messages"][0]["body"], "Please confirm")
        self.assertFalse(shortened)
        self.assertFalse(unreadable)

    def test_message_limits_are_flagged(self):
        raw = {"id": "abc123", "messages": [message(i * 1000, "x" * (MAX_BODY + 1)) for i in range(25)]}
        parsed, shortened, _ = parse_thread(raw, "club@example.com")
        self.assertTrue(shortened)
        self.assertEqual(len(parsed["messages"]), 20)
        self.assertEqual(len(parsed["messages"][0]["body"]), MAX_BODY)

    def test_embedded_links_removed_before_body_limit(self):
        text = "Log in: https://example.com/login?token=" + "secret" * 700 + "#credential\nPlease select a plan."
        parsed, shortened, _ = parse_thread({"id": "abc123", "messages": [message(1000, text)]}, "club@example.com")
        body = parsed["messages"][0]["body"]
        self.assertNotIn("secret", body)
        self.assertNotIn("credential", body)
        self.assertIn("[Link omitted]", body)
        self.assertIn("Please select a plan.", body)
        self.assertFalse(shortened)

    def test_unreadable_message_does_not_invent_content(self):
        parsed, _, unreadable = parse_thread({"id": "abc123", "messages": [message(1000, "")]}, "club@example.com")
        self.assertTrue(unreadable)
        self.assertIn("No readable text", parsed["messages"][0]["body"])


class GmailSelectionTests(unittest.TestCase):
    def setUp(self):
        self.client = GmailClient(Path("unused.json"), "club@example.com")
        self.now = datetime(2026, 10, 9, 18, 0, tzinfo=timezone.utc)

    @patch("gmail.Credentials.from_authorized_user_file")
    @patch("gmail.AuthorizedSession")
    def test_epoch_window_pagination_and_full_thread_context(self, session, credentials):
        self.client._get = Mock(side_effect=[
            {"emailAddress": "club@example.com"},
            {"threads": [{"id": "aaa"}], "nextPageToken": "page2"},
            {"threads": [{"id": "aaa"}, {"id": "bbb"}]},
            {"id": "aaa", "messages": [message(1000, "Earlier request"), message(2000, "Later response", ["SENT"])]},
            {"id": "bbb", "messages": [message(3000, "Second thread")]},
        ])
        result = self.client.select_threads("24h", now=self.now)
        self.assertEqual(len(result.threads), 2)
        self.assertEqual(result.start, self.now - timedelta(hours=24))
        query = self.client._get.call_args_list[1].args[3]["q"]
        self.assertIn(f"after:{int(result.start.timestamp())}", query)
        self.assertIn(f"before:{int(self.now.timestamp())}", query)
        self.assertIn("-in:drafts", query)
        self.assertEqual(self.client._get.call_args_list[2].args[3]["pageToken"], "page2")
        self.assertEqual(self.client._get.call_args_list[3].args[3], {"format": "full"})
        self.assertEqual(result.threads[0]["messages"][1]["direction"], "outgoing")

    @patch("gmail.Credentials.from_authorized_user_file")
    @patch("gmail.AuthorizedSession")
    def test_wrong_mailbox_rejected_before_fetching_threads(self, session, credentials):
        self.client._get = Mock(return_value={"emailAddress": "wrong@example.com"})
        with self.assertRaisesRegex(GmailError, "doesn't match"):
            self.client.select_threads("7d", now=self.now)
        self.client._get.assert_called_once()

    @patch("gmail.Credentials.from_authorized_user_file")
    @patch("gmail.AuthorizedSession")
    def test_selection_cap_is_reported(self, session, credentials):
        raw_ids = [{"id": format(i, "x")} for i in range(20)]
        self.client._get = Mock(side_effect=[
            {"emailAddress": "club@example.com"}, {"threads": raw_ids, "nextPageToken": "more"},
            *[{"id": item["id"], "messages": [message(1000, "Test")]} for item in raw_ids],
        ])
        result = self.client.select_threads("7d", now=self.now)
        self.assertEqual(len(result.threads), 20)
        self.assertIn("additional matches", result.warnings[0])

    @patch("gmail.Credentials.from_authorized_user_file", side_effect=FileNotFoundError())
    def test_missing_token_has_actionable_error(self, credentials):
        with self.assertRaisesRegex(GmailError, "authorization helper"):
            self.client.select_threads("24h")

    def test_unsupported_period_and_naive_clock_rejected(self):
        with self.assertRaises(GmailError):
            self.client.select_threads("30d")
        with self.assertRaises(GmailError):
            self.client.select_threads("24h", now=datetime(2026, 10, 9))

    def test_http_error_is_safe(self):
        session = MagicMock()
        response = session.get.return_value.__enter__.return_value
        response.status_code = 403
        response.text = "private email content"
        with self.assertRaises(GmailError) as error:
            self.client._get(session, "threads", time.monotonic() + 10)
        self.assertNotIn("private email content", str(error.exception))

    def test_streamed_response_limit(self):
        session = MagicMock()
        response = session.get.return_value.__enter__.return_value
        response.status_code = 200
        response.iter_content.return_value = [b"x" * 20]
        with patch("gmail.MAX_RESPONSE_BYTES", 10), self.assertRaisesRegex(GmailError, "too large"):
            self.client._get(session, "threads", time.monotonic() + 10)
