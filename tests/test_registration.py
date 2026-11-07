from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord
from discord import app_commands
import requests

from cogs.registration import Registration, question_stats_embed
from harp import HarpClient, HarpError, parse_question_stats, parse_registration_stats
from tests.helpers import settings


PAYLOAD = {
    "data": {
        "total_started": 62,
        "total_submitted": 40,
        "by_status": {"draft": 22, "submitted": 40, "accepted": 0, "rejected": 0, "waitlisted": 0},
    }
}

OUTREACH_PAYLOAD = {
    "data": {
        "question_id": "event_outreach",
        "label": "How did you hear about us?",
        "type": "multi_select",
        "total_submitted": 210,
        "answered": 184,
        "options": [
            {"value": "discord", "label": "Discord", "count": 76},
            {"value": "friend", "label": "Friend", "count": 120},
        ],
    }
}


class HarpClientTests(unittest.TestCase):
    def test_settings_repr_hides_key(self):
        self.assertNotIn("private-test-key", repr(settings(HARP_BOT_API_KEY="private-test-key")))

    @patch("harp.requests.get")
    def test_parses_mocked_response_and_sends_bearer_key(self, get):
        get.return_value.status_code = 200
        get.return_value.json.return_value = PAYLOAD
        stats = HarpClient("private-test-key").registration_stats()
        self.assertEqual(stats.total_started, 62)
        self.assertEqual(stats.total_submitted, 40)
        self.assertEqual(stats.by_status["draft"], 22)
        self.assertEqual(get.call_args.args[0], "https://portal.tidaltamu.com/v1/integrations/registration/stats")
        self.assertEqual(get.call_args.kwargs["headers"], {"Authorization": "Bearer private-test-key"})
        self.assertEqual(get.call_args.kwargs["timeout"], 5)

    def test_rejects_malformed_counts(self):
        for value in ("40", -1, True):
            with self.subTest(value=value), self.assertRaises(HarpError):
                parse_registration_stats({"data": {**PAYLOAD["data"], "total_submitted": value}})

    @patch("harp.requests.get")
    def test_safe_errors_do_not_expose_response_or_key(self, get):
        get.side_effect = requests.Timeout("private-test-key")
        with self.assertRaises(HarpError) as caught:
            HarpClient("private-test-key").registration_stats()
        self.assertNotIn("private-test-key", str(caught.exception))

        get.side_effect = None
        get.return_value.status_code = 503
        get.return_value.text = "private response"
        with self.assertRaises(HarpError) as caught:
            HarpClient("private-test-key").registration_stats()
        self.assertNotIn("private response", str(caught.exception))

        get.return_value.status_code = 200
        get.return_value.json.side_effect = ValueError("private response")
        with self.assertRaises(HarpError) as caught:
            HarpClient("private-test-key").registration_stats()
        self.assertNotIn("private response", str(caught.exception))

    @patch("harp.requests.get")
    def test_missing_key_skips_request(self, get):
        with self.assertRaisesRegex(HarpError, "HARP_BOT_API_KEY"):
            HarpClient("").registration_stats()
        get.assert_not_called()


class RegistrationCommandTests(unittest.IsolatedAsyncioTestCase):
    @patch("harp.requests.get")
    async def test_command_output_from_mocked_harp_response(self, get):
        get.return_value.status_code = 200
        get.return_value.json.return_value = PAYLOAD
        bot = SimpleNamespace(settings=settings(HARP_BOT_API_KEY="private-test-key"))
        cog = Registration(bot)
        interaction = SimpleNamespace(
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        await cog.registration_stats.callback(cog, interaction)
        interaction.response.defer.assert_awaited_once_with(thinking=True)
        sent = interaction.followup.send.call_args
        self.assertNotIn("ephemeral", sent.kwargs)
        embed = sent.kwargs["embed"]
        self.assertIn("40 applications submitted", embed.description)
        self.assertIn("non-draft", embed.description)
        self.assertIn("62 (including drafts)", embed.fields[0].value)
        for status, count in PAYLOAD["data"]["by_status"].items():
            self.assertIn(f"{status.title()}: **{count}**", embed.fields[1].value)
        self.assertNotIn("private-test-key", str(embed.to_dict()))


class OutreachStatsTests(unittest.IsolatedAsyncioTestCase):
    def test_zero_answers_and_retired_choices_use_harp_totals(self):
        data = OUTREACH_PAYLOAD["data"]
        for answered, count, expected in [(0, 0, "0.0%"), (184, 76, "41.3%")]:
            with self.subTest(answered=answered):
                options = [data["options"][0] | {"count": count}]
                payload = {"data": data | {"answered": answered, "options": options}}
                stats = parse_question_stats(payload, "event_outreach")
                description = question_stats_embed(stats).description
                self.assertIn(f"**{answered} of 210 answered**", description)
                self.assertIn(f"Discord · {count} ({expected})", description)

    @patch("harp.requests.get")
    async def test_outreach_fetches_configured_question_and_posts_in_channel(self, get):
        get.return_value.status_code = 200
        get.return_value.json.return_value = OUTREACH_PAYLOAD
        bot = SimpleNamespace(settings=settings(
            HARP_BOT_API_KEY="private-test-key", HARP_OUTREACH_QUESTION_ID="event_outreach",
        ))
        cog = Registration(bot)
        member = Mock(spec=discord.Member, roles=[SimpleNamespace(id=123)])
        request = SimpleNamespace(
            client=bot,
            user=member,
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        command = cog.appstats.get_command("outreach")
        await command._invoke_with_namespace(request, SimpleNamespace())
        get.assert_called_once_with(
            "https://portal.tidaltamu.com/v1/integrations/application/questions/event_outreach/stats",
            headers={"Authorization": "Bearer private-test-key"},
            timeout=5,
        )
        request.response.defer.assert_awaited_once_with(thinking=True)
        sent = request.followup.send.call_args.kwargs
        self.assertFalse(sent.get("ephemeral", False))
        self.assertEqual(sent["embed"].title, "How did you hear about us?")
        self.assertIn("**184 of 210 answered**", sent["embed"].description)
        self.assertIn("Discord · 76 (41.3%)", sent["embed"].description)
        self.assertIn("Friend · 120 (65.2%)", sent["embed"].description)

        member.roles = []
        with self.assertRaises(app_commands.CheckFailure):
            await command._invoke_with_namespace(request, SimpleNamespace())
        self.assertEqual(get.call_count, 1)

    @patch("harp.requests.get")
    def test_outreach_status_errors_are_actionable(self, get):
        get.return_value.text = "private response"
        for status, expected in [(401, "HARP_BOT_API_KEY"), (403, "application_questions:read"), (404, "allowlist")]:
            with self.subTest(status=status):
                get.return_value.status_code = status
                with self.assertRaisesRegex(HarpError, expected) as failure:
                    HarpClient("private-test-key").question_stats("event_outreach")
                self.assertNotIn("private-test-key", str(failure.exception))
                self.assertNotIn("private response", str(failure.exception))
