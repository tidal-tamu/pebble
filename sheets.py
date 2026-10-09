import gspread
from google.oauth2.service_account import Credentials
import logging

from config import ConfigurationError, Settings

logger = logging.getLogger(__name__)

# Sheet/tab names as they appear in the F26 Tracker
TASKS_SHEET = "Tasks"
OFFICER_LIST_SHEET = "Officer List"


class SheetsError(RuntimeError):
    """A readable error that can be shown privately to a command caller."""


class SheetsClient:
    def __init__(self, settings: Settings):
        self.scopes = [
            "https://www.googleapis.com/auth/spreadsheets.readonly",
            "https://www.googleapis.com/auth/drive.readonly",
        ]
        self.credentials_file = settings.credentials_file
        self.spreadsheet_id = settings.spreadsheet_id

        try:
            self.credentials = Credentials.from_service_account_file(
                self.credentials_file, scopes=self.scopes
            )
            self.client = gspread.authorize(self.credentials)
            self.client.set_timeout((5, 20))
        except Exception as error:
            raise ConfigurationError(
                "Can't load Google credentials. Check GOOGLE_CREDENTIALS_FILE and the service-account JSON key."
            ) from error

    def get_sheet(self, sheet_name=TASKS_SHEET):
        try:
            spreadsheet = self.client.open_by_key(self.spreadsheet_id)
            return spreadsheet.worksheet(sheet_name)
        except Exception as e:
            logger.error("Access to worksheet %s failed (%s)", sheet_name, type(e).__name__)
            raise SheetsError(
                f"Couldn't access the {sheet_name} tab. Check spreadsheet sharing, tab names, and Google API access."
            ) from e

    def get_all_tasks(self, sheet_name=TASKS_SHEET):
        """
        Returns every row of the Tasks tab as a dict keyed by the header row.
        Expected headers: Task | Priority | Team | Officer | Status | Link
        """
        sheet = self.get_sheet(sheet_name)
        try:
            return sheet.get_all_records()
        except Exception as e:
            logger.error("Reading tasks failed (%s)", type(e).__name__)
            raise SheetsError("Couldn't read tasks. Check the Tasks header row and try again.") from e

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
        try:
            # Preserve large Discord IDs as strings during gspread's conversion.
            rows = sheet.get_all_records(numericise_ignore=['all'])
        except Exception as e:
            logger.error("Reading officers failed (%s)", type(e).__name__)
            raise SheetsError("Couldn't read the Officer List. Check its header row and try again.") from e

        full_name_map = {}
        first_name_index = {}
        first_name_collisions = set()

        for row in rows:
            row = {str(key).strip().lower(): value for key, value in row.items()}
            name = (
                row.get('name')
                or row.get('officer')
                or row.get('officer name')
                or ''
            )
            discord_id = (
                row.get('discord id')
                or row.get('discordid')
                or row.get('discord')
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
