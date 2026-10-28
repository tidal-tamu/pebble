import gspread
from gspread.exceptions import APIError
from gspread.utils import ValueInputOption, rowcol_to_a1
from google.oauth2.service_account import Credentials
from datetime import date
import hashlib
import json
import logging

from config import ConfigurationError, Settings

logger = logging.getLogger(__name__)

# Sheet/tab names as they appear in the F26 Tracker
TASKS_SHEET = "Tasks"
OFFICER_LIST_SHEET = "Officer List"


class SheetsError(RuntimeError):
    """A readable error that can be shown privately to a command caller."""


def _typed(value):
    """The text an officer would type into a cell to enter `value`."""
    if isinstance(value, date):
        return value.strftime("%m/%d/%Y")
    value = str(value)
    # A leading apostrophe makes Sheets store formula-like text literally.
    return "'" + value if value.startswith(("=", "+", "-", "@")) else value


def _record(headers, cells):
    """{header: cell text} for the labeled columns of one row."""
    return {name: str(cells[i]) if i < len(cells) else "" for i, name in enumerate(headers) if name}


def task_key(record):
    """
    A short fingerprint of a task row's cells other than Status. The tracker
    has no ID column, so this is how a task picked from a list is recognized
    again after rows move or its status changes.
    """
    cells = sorted((name, value.strip()) for name, value in record.items() if name != "Status")
    return hashlib.sha256(json.dumps(cells).encode()).hexdigest()[:16]


def _ambiguous_first_names(officers):
    """Lowercase first names shared by officers with different Discord IDs."""
    ids_by_first = {}
    for name, discord_id in officers:
        ids_by_first.setdefault(name.split()[0].lower(), set()).add(discord_id)
    return {first for first, ids in ids_by_first.items() if len(ids) > 1}


class SheetsClient:
    def __init__(self, settings: Settings):
        self.scopes = [
            # Write access is used only to append rows for /create-task and
            # to set a task's Status cell for /complete-task.
            "https://www.googleapis.com/auth/spreadsheets",
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
        Expected headers: Task | Priority | Team | Officer | Status | Link | Notes
        """
        sheet = self.get_sheet(sheet_name)
        try:
            return sheet.get_all_records()
        except Exception as e:
            logger.error("Reading tasks failed (%s)", type(e).__name__)
            raise SheetsError("Couldn't read tasks. Check the Tasks header row and try again.") from e

    def append_task(self, task, sheet_name=TASKS_SHEET):
        """
        Writes `task` ({header: value}) in the row below the last row with a
        value in any labeled column, and returns that row number. Values are
        entered as if an officer typed them, so dates become date cells and
        dropdown values match their options.
        """
        sheet = self.get_sheet(sheet_name)
        try:
            rows = sheet.get_all_values()
        except Exception as e:
            logger.error("Reading tasks failed (%s)", type(e).__name__)
            raise SheetsError("Couldn't read the Tasks tab. Please try again.") from e

        headers = [str(cell).strip() for cell in rows[0]]
        missing = [name for name in task if name not in headers]
        if missing:
            raise SheetsError(f"The Tasks header row is missing {', '.join(missing)}. Check row 1 of the tab.")

        labeled = [i for i, name in enumerate(headers) if name]
        last_row = max(n for n, row in enumerate(rows, 1) if any(str(row[i]).strip() for i in labeled))
        target = last_row + 1
        # The API skips None, so unlabeled columns keep whatever they hold.
        values = [_typed(task[name]) if name in task else None for name in headers]

        try:
            if target > sheet.row_count:
                sheet.add_rows(target - sheet.row_count)
            sheet.update(
                [values],
                f"{rowcol_to_a1(target, 1)}:{rowcol_to_a1(target, len(headers))}",
                value_input_option=ValueInputOption.user_entered,
            )
        except APIError as e:
            logger.error("Adding a task failed (HTTP %s)", e.code)
            if e.code == 403:
                raise SheetsError(
                    "I can't edit the tracker. Share it with the service account's email as an Editor."
                ) from e
            raise SheetsError("Couldn't add the task to the Tasks tab. Please try again.") from e
        except Exception as e:
            logger.error("Adding a task failed (%s)", type(e).__name__)
            raise SheetsError("Couldn't add the task to the Tasks tab. Please try again.") from e
        return target

    def get_task_rows(self, sheet_name=TASKS_SHEET):
        """
        Returns [(row_number, {header: text})] for every row below the header,
        with cells as displayed. Row numbers shift when rows are inserted,
        deleted, or sorted, so check task_key before relying on one later.
        """
        sheet = self.get_sheet(sheet_name)
        try:
            rows = sheet.get_all_values()
        except Exception as e:
            logger.error("Reading tasks failed (%s)", type(e).__name__)
            raise SheetsError("Couldn't read the Tasks tab. Please try again.") from e
        if not rows:
            return []
        headers = [str(cell).strip() for cell in rows[0]]
        return [(n, _record(headers, row)) for n, row in enumerate(rows[1:], 2)]

    def set_task_status(self, row_number, key, status, sheet_name=TASKS_SHEET):
        """
        Enters `status` in the Status cell of `row_number`, leaving the rest of
        the row unchanged. Rereads the row first and writes nothing unless its
        task_key still equals `key`.
        """
        sheet = self.get_sheet(sheet_name)
        try:
            header, row = sheet.batch_get(["1:1", f"{row_number}:{row_number}"])
        except Exception as e:
            logger.error("Reading tasks failed (%s)", type(e).__name__)
            raise SheetsError("Couldn't read the Tasks tab. Please try again.") from e

        headers = [str(cell).strip() for cell in (header[0] if header else [])]
        if "Status" not in headers:
            raise SheetsError("The Tasks header row is missing Status. Check row 1 of the tab.")
        if task_key(_record(headers, row[0] if row else [])) != key:
            raise SheetsError("That task moved or changed while I was updating it. Nothing was changed; please try again.")

        try:
            sheet.update(
                [[_typed(status)]],
                rowcol_to_a1(row_number, headers.index("Status") + 1),
                value_input_option=ValueInputOption.user_entered,
            )
        except APIError as e:
            logger.error("Updating a task failed (HTTP %s)", e.code)
            if e.code == 403:
                raise SheetsError(
                    "I can't edit the tracker. Share it with the service account's email as an Editor."
                ) from e
            raise SheetsError("Couldn't update the task's status. Please try again.") from e
        except Exception as e:
            logger.error("Updating a task failed (%s)", type(e).__name__)
            raise SheetsError("Couldn't update the task's status. Please try again.") from e

    def _read_officers(self, sheet_name):
        """Returns [(name, discord_id)] for Officer List rows that have both."""
        sheet = self.get_sheet(sheet_name)
        try:
            # Preserve large Discord IDs as strings during gspread's conversion.
            rows = sheet.get_all_records(numericise_ignore=['all'])
        except Exception as e:
            logger.error("Reading officers failed (%s)", type(e).__name__)
            raise SheetsError("Couldn't read the Officer List. Check its header row and try again.") from e

        officers = []
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
            if name and discord_id:
                officers.append((name, discord_id))
        return officers

    def get_officer_names(self, sheet_name=OFFICER_LIST_SHEET):
        """
        Returns {discord_id: name} using the names the Tasks tab expects: the
        first name, or the full name when another officer shares that first
        name. get_officer_id_map resolves either form to the same Discord ID.
        """
        officers = self._read_officers(sheet_name)
        collisions = _ambiguous_first_names(officers)
        names = {}
        for name, discord_id in officers:
            first = name.split()[0]
            names.setdefault(discord_id, name if first.lower() in collisions else first)
        return names

    def get_officer_id_map(self, sheet_name=OFFICER_LIST_SHEET):
        """
        Reads the Officer List tab and returns {lookup_key: discord_id}.
        Expected headers (case-insensitive): Name (or Officer) | Discord ID.

        Lookup keys include the full name and the first name (lowercase),
        because the Tasks tab sometimes uses first-name-only chips
        (e.g. "Thalia" -> "Thalia Johnson"). First-name aliases are only
        added when they're unambiguous across the roster.
        """
        officers = self._read_officers(sheet_name)
        collisions = _ambiguous_first_names(officers)

        mapping = {name.lower(): discord_id for name, discord_id in officers}
        for name, discord_id in officers:
            first = name.split()[0].lower()
            if first not in collisions:
                mapping.setdefault(first, discord_id)

        if collisions:
            logger.info(
                "Ambiguous first names (won't auto-resolve): "
                + ", ".join(sorted(collisions))
            )

        return mapping
