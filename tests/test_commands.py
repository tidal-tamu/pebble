import asyncio
from datetime import date, timedelta
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord
from discord import app_commands

from checks import is_officer
from cogs.announcements import Announcements
from cogs.reminders import Reminders, _parse_due_date, _row_is_pending, _split_officers, _join_lines_for_embed
from errors import respond_error
from sheets import SheetsError
from tests.helpers import settings


def interaction():
    return SimpleNamespace(
        response=SimpleNamespace(is_done=Mock(return_value=True), defer=AsyncMock(), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        command=SimpleNamespace(name="tasks"),
        user=SimpleNamespace(id=111, display_name="Test Officer"),
    )


class TaskTests(unittest.TestCase):
    def test_due_date_formats(self):
        for raw in ["10/09/2026", "10/09/26", "2026-10-09", "10-09-2026"]:
            self.assertEqual(_parse_due_date(raw), date(2026, 10, 9))
        self.assertIsNone(_parse_due_date("high"))

    def test_completed_and_blank_tasks_are_excluded(self):
        self.assertFalse(_row_is_pending({"Task": "Test", "Status": " Completed "}))
        self.assertFalse(_row_is_pending({"Task": "Test", "Status": "DONE"}))
        self.assertFalse(_row_is_pending({"Task": " "}))
        self.assertTrue(_row_is_pending({"Task": "Test", "Status": "In progress"}))

    def test_multiple_assignees(self):
        self.assertEqual(_split_officers("Alex, Sam; Taylor\nJo"), ["Alex", "Sam", "Taylor", "Jo"])

    def test_long_task_list_stays_within_embed_limit(self):
        description, truncated = _join_lines_for_embed(["x" * 500] * 20)
        self.assertTrue(truncated)
        self.assertLessEqual(len(description), 4096)


class CommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.channel = SimpleNamespace(name="test", send=AsyncMock())
        self.bot = SimpleNamespace(settings=settings(), get_channel=Mock(return_value=self.channel))
        with patch("cogs.reminders.SheetsClient"):
            self.cog = Reminders(self.bot)

    async def test_sweep_cutoff_completion_and_assignee_matching(self):
        today = date.today()
        def task(name, days, officer="Alex", status="Pending"):
            return {"Task": name, "Priority": (today + timedelta(days=days)).isoformat(), "Officer": officer, "Status": status}
        rows = [task("Overdue", -1), task("At cutoff", 7), task("Later", 8), task("Done", -2, status="Done"), task("Unknown", 0, officer="Missing"), {"Task": "Undated", "Officer": "Alex"}]
        self.cog.sheets_client.get_all_tasks.return_value = rows
        self.cog.sheets_client.get_officer_id_map.return_value = {"alex": "111"}
        result = await self.cog._run_sweep()
        self.assertEqual(result.sent, 3)
        self.assertEqual(result.skipped_done, 1)
        self.assertEqual(result.skipped_future, 1)
        self.assertEqual(result.unmapped_officers, ["Missing"])
        for call in self.channel.send.call_args_list:
            self.assertEqual(call.kwargs["content"], "<@111>")
            self.assertFalse(call.kwargs["allowed_mentions"].everyone)
            self.assertFalse(call.kwargs["allowed_mentions"].roles)

    async def test_slow_sheets_read_does_not_block_discord_event_loop(self):
        started = threading.Event()
        release = threading.Event()
        def slow_read():
            started.set()
            release.wait(timeout=3)
            return []
        self.cog.sheets_client.get_all_tasks.side_effect = slow_read
        task = asyncio.create_task(self.cog._read_tracker())
        try:
            for _ in range(100):
                if started.is_set():
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(started.is_set())
            self.assertFalse(task.done())
        finally:
            release.set()
            await task

    async def test_overlapping_reminder_command_is_rejected(self):
        request = interaction()
        async with self.cog._sweep_lock:
            await self.cog.remind.callback(self.cog, request)
        self.channel.send.assert_not_called()
        self.assertIn("already running", request.followup.send.call_args.args[0])

    async def test_partial_delivery_failure_reports_count(self):
        self.cog.sheets_client.get_all_tasks.return_value = [{"Task": "A", "Officer": "Alex"}, {"Task": "B", "Officer": "Alex"}]
        self.cog.sheets_client.get_officer_id_map.return_value = {"alex": "111"}
        self.channel.send.side_effect = [None, RuntimeError("private response")]
        result = await self.cog._run_sweep()
        self.assertEqual(result.sent, 1)
        self.assertIn("after 1", result.error)
        self.assertNotIn("private response", result.error)

    async def test_error_reply_works_before_and_after_defer(self):
        for deferred in [False, True]:
            request = interaction()
            request.response.is_done.return_value = deferred
            await respond_error(request, SheetsError("Check sharing."))
            sender = request.followup.send if deferred else request.response.send_message
            sender.assert_awaited_once_with("Check sharing.", ephemeral=True)

    async def test_unexpected_error_does_not_expose_api_response(self):
        request = interaction()
        await respond_error(request, RuntimeError("private response"))
        self.assertNotIn("private response", request.followup.send.call_args.args[0])

    async def test_officer_check_denies_missing_role_and_direct_messages(self):
        @is_officer()
        async def command(request):
            pass
        predicate = command.__discord_app_commands_checks__[0]
        member = Mock(spec=discord.Member)
        member.roles = [SimpleNamespace(id=123)]
        request = SimpleNamespace(client=self.bot, user=member)
        self.assertTrue(await predicate(request))
        member.roles = []
        self.assertFalse(await predicate(request))
        request.user = SimpleNamespace(id=111)
        self.assertFalse(await predicate(request))
        request.client = SimpleNamespace()
        self.assertFalse(await predicate(request))

    async def test_announcement_defers_and_confirms_delivery(self):
        request = interaction()
        channel = SimpleNamespace(send=AsyncMock(), mention="#test")
        cog = Announcements(self.bot)
        await cog.announce.callback(cog, request, channel, "Title", "Message")
        request.response.defer.assert_awaited_once()
        channel.send.assert_awaited_once()
        self.assertTrue(request.followup.send.call_args.kwargs["ephemeral"])

    async def test_oversized_announcement_is_rejected_before_delivery(self):
        request = interaction()
        channel = SimpleNamespace(send=AsyncMock())
        cog = Announcements(self.bot)
        await cog.announce.callback(cog, request, channel, "x" * 257, "Message")
        channel.send.assert_not_called()
        request.response.send_message.assert_awaited_once()
