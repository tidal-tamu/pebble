import unittest

from config import ConfigurationError, ROOT
from tests.helpers import settings


class ConfigurationTests(unittest.TestCase):
    def test_missing_and_placeholder_values_are_rejected(self):
        for key in ["DISCORD_TOKEN", "SPREADSHEET_ID", "OFFICER_ROLE_ID", "REMINDER_CHANNEL_ID"]:
            for value in ["", "your_placeholder"]:
                with self.subTest(key=key, value=value), self.assertRaises(ConfigurationError):
                    settings(**{key: value})

    def test_discord_ids_are_positive_numbers(self):
        for value in ["abc", "-1", "0", "12.3"]:
            with self.subTest(value=value), self.assertRaises(ConfigurationError):
                settings(OFFICER_ROLE_ID=value)

    def test_spreadsheet_url_is_rejected_with_specific_guidance(self):
        with self.assertRaisesRegex(ConfigurationError, "not a URL"):
            settings(SPREADSHEET_ID="https://docs.google.com/spreadsheets/d/abc/edit")

    def test_guild_id_can_be_omitted(self):
        self.assertIsNone(settings(GUILD_ID="").guild_id)

    def test_participant_role_ids_are_optional_positive_ids(self):
        for name in ("JUDGE_ROLE_ID", "MENTOR_ROLE_ID", "HACKER_ROLE_ID"):
            self.assertIsNone(getattr(settings(), name.lower()))
            self.assertEqual(getattr(settings(**{name: "1001"}), name.lower()), 1001)
            for value in ("abc", "0", "-1"):
                with self.subTest(name=name, value=value), self.assertRaises(ConfigurationError):
                    settings(**{name: value})

    def test_credentials_are_relative_to_repository(self):
        self.assertEqual(settings().credentials_file, ROOT / "service_account.json")

    def test_token_is_not_in_settings_repr(self):
        self.assertNotIn("test-token", repr(settings()))

    def test_unknown_log_level_is_rejected(self):
        with self.assertRaises(ConfigurationError):
            settings(LOG_LEVEL="verbose")
