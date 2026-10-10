import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from discord import app_commands

from config import ConfigurationError
from cogs.emails import Emails
from email_summary import EmailSummaryService, build_batches, summary_pages
from gemini import MAX_INPUT_CHARACTERS
from tests.helpers import settings


def digest_for(threads):
    return {"overview": "Summary", "action_items": [{"thread_id": threads[0]["thread_id"], "action": "Send logo", "owner": "Morgan", "deadline": "Not specified"}], "awaiting_our_reply": [], "waiting_on_others": []}


class SummaryTests(unittest.TestCase):
    def test_demo_does_not_read_gmail(self):
        service = EmailSummaryService(settings(EMAIL_SUMMARY_MODE="demo", GEMINI_API_KEY="test-key"))
        service.gemini.summarize = Mock(side_effect=digest_for)
        with patch("email_summary.GmailClient") as gmail:
            result = service.run("24h")
        gmail.assert_not_called()
        self.assertTrue(result.demo)
        self.assertEqual(len(result.selection.threads), 4)
        self.assertIn("fictional", result.selection.warnings[0])

    def test_batches_are_bounded_and_omissions_reported(self):
        threads = [{"thread_id": str(i), "messages": [{"body": "x" * 20000}]} for i in range(10)]
        batches, warnings = build_batches(threads)
        self.assertEqual(len(batches), 3)
        for batch in batches:
            self.assertLessEqual(len(json.dumps({"threads": batch}, ensure_ascii=False)), MAX_INPUT_CHARACTERS)
        self.assertTrue(any("omitted" in warning for warning in warnings))
        self.assertEqual(len(threads), 10)

    def test_large_thread_keeps_latest_context_and_reports_loss(self):
        threads = [{"thread_id": "abc", "messages": [{"body": "x" * 20000}, {"body": "y" * 20000}, {"body": "latest reply"}]}]
        batches, warnings = build_batches(threads)
        self.assertEqual(batches[0][0]["messages"][-1]["body"], "latest reply")
        self.assertTrue(warnings)
        self.assertEqual(len(threads[0]["messages"]), 3)

    def test_pages_escape_mentions_and_stay_within_limits(self):
        service = EmailSummaryService(settings(EMAIL_SUMMARY_MODE="demo", GEMINI_API_KEY="test-key"))
        service.gemini.summarize = Mock(side_effect=digest_for)
        result = service.run("24h")
        result.digest["overview"] = "@everyone " + "x" * 9000
        pages = summary_pages(result)
        self.assertGreater(len(pages), 1)
        self.assertTrue(all(len(page) <= 3500 for page in pages))
        self.assertNotIn("@everyone", "".join(pages))
        self.assertIn("fictional", pages[0])
        self.assertNotIn("mail.google.com", "".join(pages))

    def test_live_uses_expected_mailbox_and_links_actual_sources(self):
        from datetime import datetime, timezone
        from gmail import ThreadSelection
        service = EmailSummaryService(settings(EMAIL_SUMMARY_MODE="live", GEMINI_API_KEY="test-key", GMAIL_EXPECTED_ACCOUNT="club@example.com"))
        service.gemini.summarize = Mock(side_effect=digest_for)
        now = datetime.now(timezone.utc)
        source = {"thread_id": "abc123", "subject": "Logo", "messages": [{"body": "Please send logo"}]}
        with patch("email_summary.GmailClient") as gmail:
            gmail.return_value.select_threads.return_value = ThreadSelection([source], [], now, now, "club@example.com")
            result = service.run("7d")
        gmail.return_value.select_threads.assert_called_once_with("7d")
        pages = "".join(summary_pages(result))
        self.assertIn("#all/abc123", pages)
        self.assertIn("authuser=club%40example.com", pages)

    def test_empty_mailbox_does_not_call_gemini(self):
        from datetime import datetime, timezone
        from gmail import ThreadSelection
        service = EmailSummaryService(settings(EMAIL_SUMMARY_MODE="live", GEMINI_API_KEY="test-key", GMAIL_EXPECTED_ACCOUNT="club@example.com"))
        service.gemini.summarize = Mock()
        now = datetime.now(timezone.utc)
        with patch("email_summary.GmailClient") as gmail:
            gmail.return_value.select_threads.return_value = ThreadSelection([], [], now, now, "club@example.com")
            result = service.run("24h")
        service.gemini.summarize.assert_not_called()
        self.assertIn("No email", result.digest["overview"])

    def test_optional_configuration_requires_explicit_mode(self):
        self.assertEqual(settings().email_summary_mode, "disabled")
        with self.assertRaises(ConfigurationError):
            settings(EMAIL_SUMMARY_MODE="demo")
        with self.assertRaises(ConfigurationError):
            settings(EMAIL_SUMMARY_MODE="live", GEMINI_API_KEY="test-key")
        self.assertNotIn("test-key", repr(settings(GEMINI_API_KEY="test-key")))


class EmailCommandTests(unittest.IsolatedAsyncioTestCase):
    def request(self):
        return SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))

    async def test_disabled_mode_does_not_call_service(self):
        cog = Emails(SimpleNamespace(settings=settings()))
        cog.service.run = Mock()
        request = self.request()
        await cog.summarize_emails.callback(cog, request, app_commands.Choice(name="Last 24 hours", value="24h"))
        cog.service.run.assert_not_called()
        self.assertTrue(request.response.send_message.call_args.kwargs["ephemeral"])

    async def test_demo_pages_are_private_and_mentions_disabled(self):
        cog = Emails(SimpleNamespace(settings=settings(EMAIL_SUMMARY_MODE="demo", GEMINI_API_KEY="test-key")))
        cog.service.gemini.summarize = Mock(side_effect=digest_for)
        request = self.request()
        await cog.summarize_emails.callback(cog, request, app_commands.Choice(name="Last 24 hours", value="24h"))
        request.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        self.assertTrue(request.followup.send.call_args.kwargs["ephemeral"])
        self.assertIn("DEMO", request.followup.send.call_args.kwargs["embed"].title)
        self.assertFalse(request.followup.send.call_args.kwargs["allowed_mentions"].everyone)

    async def test_concurrent_command_is_rejected(self):
        cog = Emails(SimpleNamespace(settings=settings(EMAIL_SUMMARY_MODE="demo", GEMINI_API_KEY="test-key")))
        cog.service.run = Mock()
        request = self.request()
        async with cog._lock:
            await cog.summarize_emails.callback(cog, request, app_commands.Choice(name="Last 24 hours", value="24h"))
        cog.service.run.assert_not_called()
        self.assertIn("already running", request.response.send_message.call_args.args[0])

    async def test_cooldown_prevents_second_request(self):
        cog = Emails(SimpleNamespace(settings=settings(EMAIL_SUMMARY_MODE="demo", GEMINI_API_KEY="test-key")))
        cog.service.gemini.summarize = Mock(side_effect=digest_for)
        period = app_commands.Choice(name="Last 24 hours", value="24h")
        await cog.summarize_emails.callback(cog, self.request(), period)
        second = self.request()
        await cog.summarize_emails.callback(cog, second, period)
        self.assertIn("wait a minute", second.response.send_message.call_args.args[0])
        cog.service.gemini.summarize.assert_called_once()
