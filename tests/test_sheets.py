import unittest
from unittest.mock import Mock, patch

from gspread.exceptions import APIError
from gspread.utils import ValueInputOption

from sheets import SheetsClient, SheetsError, task_key
from tests.helpers import settings

HEADERS = ["Task", "Priority", "Team", "Officer", "Status", "Link", "Notes"]
ROW = ["Book GBM room", "10/31/2026", "Activities", "Jamie", "In Progress"]


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

    def test_task_rows_keep_sheet_row_numbers(self):
        self.sheet.get_all_values.return_value = [HEADERS, ROW, [], ["Order snacks"]]
        rows = self.client.get_task_rows()
        self.assertEqual([n for n, _ in rows], [2, 3, 4])
        self.assertEqual(rows[0][1]["Officer"], "Jamie")
        self.assertEqual(rows[2][1], {name: "Order snacks" if name == "Task" else "" for name in HEADERS})

    def test_status_update_rechecks_the_row_and_writes_only_status(self):
        key = task_key(dict(zip(HEADERS, ROW + ["", ""])))
        self.sheet.batch_get.return_value = [[HEADERS], [ROW]]
        self.client.set_task_status(7, key, "Completed")
        self.sheet.batch_get.assert_called_once_with(["1:1", "7:7"])
        self.sheet.update.assert_called_once_with([["Completed"]], "E7", value_input_option=ValueInputOption.user_entered)

        self.sheet.update.reset_mock()
        self.sheet.batch_get.return_value = [[HEADERS], [["Order snacks"] + ROW[1:]]]
        with self.assertRaisesRegex(SheetsError, "Nothing was changed"):
            self.client.set_task_status(7, key, "Completed")
        self.sheet.update.assert_not_called()

    def test_status_update_failures_have_actionable_errors(self):
        key = task_key(dict(zip(HEADERS, ROW + ["", ""])))
        self.sheet.batch_get.return_value = [[HEADERS], [ROW]]
        forbidden = Mock()
        forbidden.json.return_value = {"error": {"code": 403, "message": "private API response", "status": "PERMISSION_DENIED"}}
        for failure, message in [(APIError(forbidden), "as an Editor"), (RuntimeError("private API response"), "Couldn't update")]:
            self.sheet.update.side_effect = failure
            with self.assertRaisesRegex(SheetsError, message) as raised:
                self.client.set_task_status(7, key, "Completed")
            self.assertNotIn("private API response", str(raised.exception))
