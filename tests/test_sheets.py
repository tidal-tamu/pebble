import unittest
from unittest.mock import Mock, patch

from sheets import SheetsClient, SheetsError
from tests.helpers import settings


class SheetsTests(unittest.TestCase):
    def setUp(self):
        with patch("sheets.Credentials.from_service_account_file"), patch("sheets.gspread.authorize"):
            self.client = SheetsClient(settings())
        self.sheet = Mock()
        self.client.get_sheet = Mock(return_value=self.sheet)

    def test_first_name_collisions_require_full_names(self):
        self.sheet.get_all_records.return_value = [
            {"Name": "Alex Smith", "Discord ID": "111"},
            {"Name": "Alex Jones", "Discord ID": "222"},
            {"Name": "Sam Green", "Discord ID": "333"},
        ]
        mapping = self.client.get_officer_id_map()
        self.assertNotIn("alex", mapping)
        self.assertEqual(mapping["alex smith"], "111")
        self.assertEqual(mapping["alex jones"], "222")
        self.assertEqual(mapping["sam"], "333")

    def test_header_aliases_and_mentions_preserve_large_ids(self):
        self.sheet.get_all_records.return_value = [
            {" officer ": "Taylor Blue", "discordid": "<@123456789012345678>"},
        ]
        self.assertEqual(self.client.get_officer_id_map()["taylor"], "123456789012345678")
        self.sheet.get_all_records.assert_called_once_with(numericise_ignore=["all"])

    def test_api_failure_is_distinct_from_empty_sheet(self):
        self.sheet.get_all_records.side_effect = RuntimeError("private API response")
        with self.assertRaises(SheetsError) as failure:
            self.client.get_all_tasks()
        self.assertNotIn("private API response", str(failure.exception))

    def test_empty_sheet_returns_no_tasks(self):
        self.sheet.get_all_records.return_value = []
        self.assertEqual(self.client.get_all_tasks(), [])

    def test_access_failure_has_actionable_error(self):
        del self.client.get_sheet
        self.client.client.open_by_key.side_effect = PermissionError()
        with self.assertRaisesRegex(SheetsError, "spreadsheet sharing"):
            self.client.get_sheet()
