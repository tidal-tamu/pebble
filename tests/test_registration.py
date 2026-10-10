from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import requests

from cogs.registration import Registration
from harp import HarpClient, HarpError, parse_registration_stats
from tests.helpers import settings


PAYLOAD = {
    "data": {
        "total_started": 62,
        "total_submitted": 40,
        "by_status": {"draft": 22, "submitted": 40, "accepted": 0, "rejected": 0, "waitlisted": 0},
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
