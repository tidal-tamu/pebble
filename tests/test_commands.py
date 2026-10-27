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
from cogs.reminders import Reminders, _parse_due_date, _row_is_pending, _split_officers, _join_lines_for_embed, _task_choice
from errors import respond_error
from sheets import SheetsError, task_key
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


def tracker_row(task, officer="Jamie", status="Not Started"):
    return {"Task": task, "Priority": "10/31/2026", "Team": "Activities", "Officer": officer, "Status": status, "Link": "", "Notes": ""}


class CompleteTaskTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = SimpleNamespace(settings=settings(), get_channel=Mock())
        with patch("cogs.reminders.SheetsClient"):
            self.cog = Reminders(self.bot)
        self.sheets = self.cog.sheets_client
        self.sheets.get_task_rows.return_value = [
            (2, tracker_row("Book GBM room")),
            (3, tracker_row("Order snacks", status="Completed")),
            (4, tracker_row("Book GBM room", officer="Tanvee")),
            (5, tracker_row("Print flyers", officer="Shruthika")),
        ]

    def request(self, officer=True):
        request = interaction()
        request.client = self.bot
        request.user = Mock(spec=discord.Member)
        request.user.roles = [SimpleNamespace(id=123)] if officer else []
        request.response.is_done.return_value = False
        request.response.autocomplete = AsyncMock()
        return request

    async def complete(self, selection):
        request = self.request()
        await self.cog.complete_task.callback(self.cog, request, selection)
        return request

    async def test_completes_the_picked_duplicate_and_confirms_privately(self):
        picked = tracker_row("Book GBM room", officer="Tanvee")
        request = await self.complete(_task_choice(4, picked).value)
        request.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        self.sheets.set_task_status.assert_called_once_with(4, task_key(picked), "Completed")
        sent = request.followup.send.call_args.kwargs
        self.assertTrue(sent["ephemeral"])
        self.assertEqual(sent["embed"].description, "**Book GBM room**")
        self.assertIn("row 4", sent["embed"].footer.text)

    async def test_identical_copies_use_the_picked_row_and_moved_tasks_are_followed(self):
        flyers = tracker_row("Print flyers", officer="Shruthika")
        self.sheets.get_task_rows.return_value.append((6, flyers))
        await self.complete(_task_choice(6, flyers).value)
        self.sheets.set_task_status.assert_called_once_with(6, task_key(flyers), "Completed")

        picked = tracker_row("Book GBM room", officer="Tanvee")
        self.sheets.get_task_rows.return_value = [(2, tracker_row("Book GBM room")), (3, picked)]
        self.sheets.set_task_status.reset_mock()
        await self.complete(_task_choice(4, picked).value)
        self.sheets.set_task_status.assert_called_once_with(3, task_key(picked), "Completed")

    async def test_missing_ambiguous_and_completed_tasks_are_not_written(self):
        edited = _task_choice(4, tracker_row("Book GBM room", officer="Tanvee") | {"Notes": "Old notes"}).value
        self.sheets.get_task_rows.return_value.append((6, tracker_row("Print flyers", officer="Shruthika")))
        moved_copy = _task_choice(9, tracker_row("Print flyers", officer="Shruthika")).value
        cases = {
            "Plan social": "No task in the tracker matches",
            edited: "No task in the tracker matches",
            "book gbm room": "2 tasks match",
            moved_copy: "2 tasks match",
            "Order snacks": "already marked Completed",
        }
        for selection, message in cases.items():
            with self.subTest(selection=selection):
                request = await self.complete(selection)
                self.assertIn(message, request.followup.send.call_args.args[0])
                self.assertTrue(request.followup.send.call_args.kwargs["ephemeral"])
        self.sheets.set_task_status.assert_not_called()

    async def test_sheets_failures_are_reported_privately_without_confirmation(self):
        for method in (self.sheets.get_task_rows, self.sheets.set_task_status):
            with self.subTest(method=method._mock_name):
                method.side_effect = SheetsError("Couldn't update the task's status. Please try again.")
                request = self.request()
                with self.assertRaises(SheetsError) as failure:
                    await self.cog.complete_task.callback(self.cog, request, "Print flyers")
                request.response.is_done.return_value = True
                await self.cog.complete_task_error(request, failure.exception)
                request.followup.send.assert_awaited_once_with("Couldn't update the task's status. Please try again.", ephemeral=True)
                method.side_effect = None

    async def test_non_officers_get_a_private_denial_and_no_suggestions(self):
        request = self.request(officer=False)
        with self.assertRaises(app_commands.CheckFailure) as denied:
            await self.cog.complete_task._invoke_with_namespace(request, SimpleNamespace(task="Print flyers"))
        await self.cog.complete_task_error(request, denied.exception)
        request.response.send_message.assert_awaited_once_with("You need the Officer role to use this command.", ephemeral=True)
        await self.cog.complete_task._invoke_autocomplete(request, "task", SimpleNamespace(task=""))
        request.response.autocomplete.assert_awaited_once_with([])
        self.sheets.get_task_rows.assert_not_called()
        self.sheets.set_task_status.assert_not_called()

    async def test_suggestions_list_matching_pending_tasks_from_a_recent_read(self):
        for current, expected in [("", [2, 4, 5]), ("tanvee", [4])]:
            request = self.request()
            await self.cog.complete_task._invoke_autocomplete(request, "task", SimpleNamespace(task=current))
            choices = request.response.autocomplete.call_args.args[0]
            self.assertEqual([int(choice.value.split(":")[0]) for choice in choices], expected)
        self.assertEqual(choices[0].name, "Book GBM room (row 4 · due Oct 31 · Tanvee)")
        self.sheets.get_task_rows.assert_called_once()
