import gspread
from google.oauth2.service_account import Credentials
import os
import logging

logger = logging.getLogger('discord')

# Sheet/tab names as they appear in the F26 Tracker
TASKS_SHEET = "Tasks"
OFFICER_LIST_SHEET = "Officer List"


class SheetsClient:
    def __init__(self):
        self.scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        self.credentials_file = "service_account.json"

        try:
            self.credentials = Credentials.from_service_account_file(
                self.credentials_file, scopes=self.scopes
            )
            self.client = gspread.authorize(self.credentials)
            self.spreadsheet_id = os.getenv('SPREADSHEET_ID')
        except FileNotFoundError:
            logger.error(
                "service_account.json not found. Google Sheets integration will fail."
            )
            self.client = None

    def get_sheet(self, sheet_name=TASKS_SHEET):
        if not self.client:
            return None

        try:
            spreadsheet = self.client.open_by_key(self.spreadsheet_id)
            return spreadsheet.worksheet(sheet_name)
        except Exception as e:
            logger.error(f"Error accessing worksheet '{sheet_name}': {e}")
            return None

    def get_all_tasks(self, sheet_name=TASKS_SHEET):
        """
        Returns every row of the Tasks tab as a dict keyed by the header row.
        Expected headers: Task | Priority | Team | Officer | Status | Link
        """
        sheet = self.get_sheet(sheet_name)
        if not sheet:
            return []

        try:
            return sheet.get_all_records()
        except Exception as e:
            logger.error(f"Failed to read tasks: {e}")
            return []

    def get_officer_id_map(self, sheet_name=OFFICER_LIST_SHEET):
        """
        Reads the Officer List tab and returns {lookup_key: discord_id}.
        Expected headers (case-insensitive): Name (or Officer) | Discord ID.

        Lookup keys include the full name and the first name (lowercase),
        because the Tasks tab sometimes uses first-name-only chips
        (e.g. "Thalia" -> "Thalia Johnson"). First-name aliases are only
        added when they're unambiguous across the roster.
        """
        sheet = self.get_sheet(sheet_name)
        if not sheet:
            return {}

        try:
            rows = sheet.get_all_records()
        except Exception as e:
            logger.error(f"Failed to read officer list: {e}")
            return {}

        full_name_map = {}
        first_name_index = {}
        first_name_collisions = set()

        for row in rows:
            name = (
                row.get('Name')
                or row.get('Officer')
                or row.get('Officer Name')
                or ''
            )
            discord_id = (
                row.get('Discord ID')
                or row.get('DiscordID')
                or row.get('Discord')
                or ''
            )

            name = str(name).strip()
            discord_id = ''.join(filter(str.isdigit, str(discord_id)))

            if not name or not discord_id:
                continue

            full_name_map[name.lower()] = discord_id

            first = name.split()[0].lower()
            if first in first_name_index and first_name_index[first] != discord_id:
                first_name_collisions.add(first)
            else:
                first_name_index.setdefault(first, discord_id)

        mapping = dict(full_name_map)
        for first, did in first_name_index.items():
            if first in first_name_collisions:
                continue
            mapping.setdefault(first, did)

        if first_name_collisions:
            logger.info(
                "Ambiguous first names (won't auto-resolve): "
                + ", ".join(sorted(first_name_collisions))
            )

        return mapping

    def update_task_status(self, row_index, status_col_index, new_status, sheet_name=TASKS_SHEET):
        """Update a single cell (1-based indices)."""
        sheet = self.get_sheet(sheet_name)
        if not sheet:
            return False

        try:
            sheet.update_cell(row_index, status_col_index, new_status)
            return True
        except Exception as e:
            logger.error(f"Failed to update task status: {e}")
            return False
