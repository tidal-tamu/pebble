import discord
from discord import app_commands
from discord.ext import commands
import logging
import asyncio
import re
import time
from datetime import datetime, date, timedelta
from urllib.parse import urlparse

from sheets import SheetsClient, SheetsError, task_key
from checks import is_officer
from errors import respond_error

logger = logging.getLogger(__name__)

# How many days before the due date we start nagging. Overdue tasks always fire.
REMIND_WITHIN_DAYS = 7

# Statuses we treat as "done" and skip.
DONE_STATUSES = {"completed", "done"}

# Officer cells can hold multiple names. Google Sheets multi-select chips
# render as a comma-separated string in the cell value, but be lenient.
_OFFICER_SPLIT_RE = re.compile(r'[,\n;]+')

# Discord embed description hard cap.
_EMBED_DESC_LIMIT = 4096

# Dropdown options on the Tasks tab. /create-task offers exactly these, so
# update them when the tracker's dropdowns change.
TEAMS = ("Executive", "Activities", "Marketing", "Workshops", "Finance", "Design", "Development")
STATUSES = ("Not Started", "In Progress", "Completed")
DEFAULT_STATUS = "Not Started"
COMPLETED_STATUS = "Completed"

# User mentions typed into a text option arrive as <@id>, or <@!id> from older clients.
_USER_MENTION_RE = re.compile(r'<@!?(\d+)>')

# /complete-task suggestions carry "row:task_key" so the exact row picked can be found again.
_TASK_CHOICE_RE = re.compile(r'(\d+):([0-9a-f]{16})')

# Discord autocomplete caps.
_CHOICE_LIMIT = 25
_CHOICE_NAME_LIMIT = 100

# Autocomplete fires as officers type, so suggestions reuse a recent read
# rather than calling Google on every keystroke. /complete-task rereads anyway.
_SUGGESTION_TTL_SECONDS = 30


def _parse_due_date(raw):
    """Try a handful of common date formats. Returns a date or None."""
    if not raw:
        return None
    raw = str(raw).strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _split_officers(raw):
    if not raw:
        return []
    return [n.strip() for n in _OFFICER_SPLIT_RE.split(str(raw)) if n.strip()]


def _parse_officer_mentions(raw):
    """Discord IDs from user @mentions, in order without repeats. None if anything else was typed."""
    ids = list(dict.fromkeys(_USER_MENTION_RE.findall(raw)))
    if not ids or not re.fullmatch(r'[\s,;]*', _USER_MENTION_RE.sub('', raw)):
        return None
    return ids


def _is_web_link(raw):
    try:
        parsed = urlparse(raw)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc) and not re.search(r'\s', raw)


def _row_is_pending(row):
    status = str(row.get('Status', '')).strip().lower()
    if status in DONE_STATUSES:
        return False
    return bool(str(row.get('Task', '')).strip())


def _task_choice(row_number, row):
    """Autocomplete choice for a task. The row number in the name tells duplicate descriptions apart."""
    details = [f"row {row_number}"]
    due_date = _parse_due_date(row.get('Priority'))
    if due_date:
        details.append(f"due {due_date.strftime('%b %d')}")
    officers = _split_officers(row.get('Officer'))
    if officers:
        details.append(", ".join(officers))
    suffix = f" ({' · '.join(details)})"
    task = str(row.get('Task', '')).strip()
    room = max(_CHOICE_NAME_LIMIT - len(suffix), 40)
    if len(task) > room:
        task = task[:room - 1] + "…"
    return app_commands.Choice(
        name=(task + suffix)[:_CHOICE_NAME_LIMIT],
        value=f"{row_number}:{task_key(row)}",
    )


def _find_tasks(task_rows, selection):
    """
    [(row_number, row)] for the tasks `selection` names: a suggestion's value,
    or a task description typed in full (ignoring case).
    """
    task_rows = [(n, row) for n, row in task_rows if str(row.get('Task', '')).strip()]
    picked = _TASK_CHOICE_RE.fullmatch(selection.strip())
    if picked:
        row_number, key = int(picked[1]), picked[2]
        same = [(n, row) for n, row in task_rows if task_key(row) == key]
        # Prefer the row that was picked; otherwise follow the task to where it moved.
        return [(n, row) for n, row in same if n == row_number] or same
    wanted = selection.strip().casefold()
    return [(n, row) for n, row in task_rows if str(row.get('Task', '')).strip().casefold() == wanted]


def _render_task_line(row, include_officers=False):
    """One markdown line (multi-line, actually) describing a task for embeds."""
    task_desc = str(row.get('Task', '')).strip()
    status = str(row.get('Status', '')).strip()
    team = str(row.get('Team', '')).strip()
    link = str(row.get('Link', '')).strip()
    due_date = _parse_due_date(row.get('Priority'))

    today = date.today()
    overdue = due_date is not None and due_date < today
    soon = (
        due_date is not None
        and not overdue
        and (due_date - today).days <= REMIND_WITHIN_DAYS
    )

    if overdue:
        prefix = "\u26a0\ufe0f"  # warning
    elif soon:
        prefix = "\u23f0"        # alarm clock
    else:
        prefix = "\u2022"         # bullet

    header = f"{prefix} **{task_desc}**"
    if due_date:
        date_str = due_date.strftime("%b %d, %Y")
        if overdue:
            date_str += " — overdue"
        header += f" \u2014 Due {date_str}"

    meta = []
    if team:
        meta.append(f"Team: {team}")
    if status:
        meta.append(f"Status: {status}")
    if include_officers:
        officers = _split_officers(row.get('Officer'))
        if officers:
            meta.append(f"Officers: {', '.join(officers)}")

    line = header
    if meta:
        line += "\n   " + " • ".join(meta)
    if link and link.lower() != 'link' and link.startswith('http'):
        line += f"\n   [Link]({link})"
    return line


def _join_lines_for_embed(lines):
    """Join lines into a single description, truncating to Discord's 4096 cap."""
    full = "\n\n".join(lines)
    if len(full) <= _EMBED_DESC_LIMIT:
        return full, False
    truncated = full[: _EMBED_DESC_LIMIT - 80].rsplit("\n\n", 1)[0]
    truncated += "\n\n*…truncated. See the F26 Tracker for the full list.*"
    return truncated, True


class SweepResult:
    """Container so /remind can report back to the caller."""

    def __init__(self):
        self.sent = 0
        self.skipped_done = 0
        self.skipped_future = 0
        self.unmapped_officers = []
        self.error = None  # human-readable string if sweep aborted early

    def summary(self, channel_name: str) -> str:
        if self.error:
            return f"Couldn't run the sweep: {self.error}"
        lines = [f"Sent **{self.sent}** reminder(s) to #{channel_name}."]
        if self.skipped_done:
            lines.append(f"Skipped {self.skipped_done} completed task(s).")
        if self.skipped_future:
            lines.append(
                f"Skipped {self.skipped_future} task(s) due more than {REMIND_WITHIN_DAYS} days out."
            )
        if self.unmapped_officers:
            unique = sorted(set(self.unmapped_officers))
            lines.append(
                "No Discord ID mapped for: " + ", ".join(unique)
                + ". Check the Officer List tab for spelling."
            )
        return "\n".join(lines)


class Reminders(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.sheets_client = SheetsClient(bot.settings)
        self._sheets_lock = asyncio.Lock()
        self._sweep_lock = asyncio.Lock()
        self._suggestion_cache = None  # (monotonic time, task rows) for autocomplete

    async def _read_tracker(self, include_officers=False):
        # gspread uses blocking HTTP. Serialize access to its shared session,
        # while allowing Discord's event loop to continue processing commands.
        async with self._sheets_lock:
            tasks_rows = await asyncio.to_thread(self.sheets_client.get_all_tasks)
            officer_map = (
                await asyncio.to_thread(self.sheets_client.get_officer_id_map)
                if include_officers else {}
            )
            return tasks_rows, officer_map

    async def _suggestion_rows(self):
        """Task rows for autocomplete, reread at most every _SUGGESTION_TTL_SECONDS."""
        async with self._sheets_lock:
            cache = self._suggestion_cache
            if cache is None or time.monotonic() - cache[0] > _SUGGESTION_TTL_SECONDS:
                rows = await asyncio.to_thread(self.sheets_client.get_task_rows)
                self._suggestion_cache = cache = (time.monotonic(), rows)
            return cache[1]

    def _get_reminder_channel(self):
        """Resolve the configured reminder channel, or (None, error_message)."""
        channel_id_int = self.bot.settings.reminder_channel_id
        channel = self.bot.get_channel(channel_id_int)
        if channel is None:
            return None, (
                f"I can't see channel `{channel_id_int}`. "
                "Make sure I'm in that server with View Channel permission "
                "and that the ID is correct."
            )
        return channel, None

    async def _run_sweep(self) -> SweepResult:
        """Read the F26 Tracker and post one ping per pending task."""
        result = SweepResult()

        channel, err = self._get_reminder_channel()
        if err:
            result.error = err
            return result

        tasks_rows, officer_map = await self._read_tracker(include_officers=True)
        if not tasks_rows:
            result.error = "The Tasks tab is empty."
            return result

        if not officer_map:
            result.error = (
                "Officer List is empty or missing Discord IDs. "
                "Populate the `Officer List` tab with `Name` and `Discord ID` columns."
            )
            return result

        today = date.today()
        cutoff = today + timedelta(days=REMIND_WITHIN_DAYS)
        allowed = discord.AllowedMentions(users=True, roles=False, everyone=False)

        for row in tasks_rows:
            status_raw = str(row.get('Status', '')).strip()
            status = status_raw.lower()
            if status in DONE_STATUSES:
                result.skipped_done += 1
                continue

            task_desc = str(row.get('Task', '')).strip()
            if not task_desc:
                continue

            due_date = _parse_due_date(row.get('Priority'))
            if due_date is not None and due_date > cutoff:
                result.skipped_future += 1
                continue

            officers = _split_officers(row.get('Officer'))
            if not officers:
                continue

            mentions = []
            for officer_name in officers:
                discord_id = officer_map.get(officer_name.lower())
                if discord_id:
                    mentions.append(f"<@{discord_id}>")
                else:
                    result.unmapped_officers.append(officer_name)

            if not mentions:
                continue

            team = str(row.get('Team', '')).strip()
            link = str(row.get('Link', '')).strip()
            overdue = due_date is not None and due_date < today

            title = "Task Overdue \u26a0\ufe0f" if overdue else "Task Reminder \u23f0"
            color = discord.Color.red() if overdue else discord.Color.orange()

            embed = discord.Embed(title=title, description=f"**{task_desc}**", color=color)
            if due_date:
                embed.add_field(name="Due", value=due_date.strftime("%b %d, %Y"))
            if team:
                embed.add_field(name="Team", value=team)
            if status_raw:
                embed.add_field(name="Status", value=status_raw)
            if link and link.lower() != 'link':
                embed.add_field(name="Link", value=link, inline=False)

            try:
                await channel.send(
                    content=" ".join(mentions),
                    embed=embed,
                    allowed_mentions=allowed,
                )
                result.sent += 1
            except discord.Forbidden:
                result.error = (
                    f"I'm missing permission to send in #{channel.name}. "
                    "Grant me Send Messages + Embed Links there."
                )
                return result
            except Exception as e:
                logger.error("Sending a reminder failed (%s)", type(e).__name__)
                result.error = f"Delivery failed after {result.sent} reminder(s). Please check channel access before retrying."
                return result

        return result

    # ---------- /remind ----------

    @app_commands.command(
        name="remind",
        description="Read the F26 Tracker and ping officers about pending tasks.",
    )
    @is_officer()
    async def remind(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        logger.info(
            f"/remind invoked by {interaction.user} ({interaction.user.id})"
        )

        if self._sweep_lock.locked():
            await interaction.followup.send("A reminder sweep is already running. Please wait for it to finish.", ephemeral=True)
            return

        try:
            async with self._sweep_lock:
                result = await self._run_sweep()
        except Exception as e:
            await respond_error(interaction, e)
            return

        channel, _ = self._get_reminder_channel()
        channel_name = channel.name if channel else "<unknown>"
        await interaction.followup.send(result.summary(channel_name), ephemeral=True)

    @remind.error
    async def remind_error(self, interaction, error):
        await respond_error(interaction, error)

    # ---------- /my-tasks ----------

    @app_commands.command(
        name="my-tasks",
        description="See your own pending tasks (private).",
    )
    @is_officer()
    async def my_tasks(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        tasks_rows, officer_map = await self._read_tracker(include_officers=True)
        if not tasks_rows:
            await interaction.followup.send(
                "The Tasks tab is empty.", ephemeral=True
            )
            return

        if not officer_map:
            await interaction.followup.send(
                "Officer List is empty or missing Discord IDs.", ephemeral=True
            )
            return

        caller_id = interaction.user.id
        mine = []
        for row in tasks_rows:
            if not _row_is_pending(row):
                continue
            officers = _split_officers(row.get('Officer'))
            for name in officers:
                did = officer_map.get(name.lower())
                if did and int(did) == caller_id:
                    mine.append(row)
                    break

        if not mine:
            await interaction.followup.send(
                "You're all caught up \u2014 no pending tasks assigned to you.",
                ephemeral=True,
            )
            return

        mine.sort(key=lambda r: _parse_due_date(r.get('Priority')) or date.max)
        lines = [_render_task_line(r) for r in mine]
        desc, _ = _join_lines_for_embed(lines)

        embed = discord.Embed(
            title=f"Your pending tasks ({len(mine)})",
            description=desc,
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="Only you can see this message.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @my_tasks.error
    async def my_tasks_error(self, interaction, error):
        await respond_error(interaction, error)

    # ---------- /tasks ----------

    @app_commands.command(
        name="tasks",
        description="See all pending tasks across the team (private to you).",
    )
    @is_officer()
    async def all_tasks(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        tasks_rows, _ = await self._read_tracker()
        if not tasks_rows:
            await interaction.followup.send(
                "The Tasks tab is empty.", ephemeral=True
            )
            return

        pending = [row for row in tasks_rows if _row_is_pending(row)]
        if not pending:
            await interaction.followup.send(
                "No pending tasks \u2014 the team is fully caught up.",
                ephemeral=True,
            )
            return

        pending.sort(key=lambda r: _parse_due_date(r.get('Priority')) or date.max)
        lines = [_render_task_line(r, include_officers=True) for r in pending]
        desc, truncated = _join_lines_for_embed(lines)

        title = f"All pending tasks ({len(pending)})"
        if truncated:
            title += " — truncated"

        embed = discord.Embed(
            title=title,
            description=desc,
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="Only you can see this message.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @all_tasks.error
    async def all_tasks_error(self, interaction, error):
        await respond_error(interaction, error)

    # ---------- /create-task ----------

    @app_commands.command(
        name="create-task",
        description="Add a task to the end of the F26 Tracker.",
    )
    @app_commands.describe(
        task="The task description",
        priority="Due date as MM/DD/YY",
        team="The team responsible for the task",
        officer="@ the officer(s) responsible for the task",
        status="Defaults to Not Started",
        link="Optional link starting with http:// or https://",
        notes="Optional notes",
    )
    @app_commands.choices(
        team=[app_commands.Choice(name=team, value=team) for team in TEAMS],
        status=[app_commands.Choice(name=status, value=status) for status in STATUSES],
    )
    @is_officer()
    async def create_task(
        self,
        interaction: discord.Interaction,
        task: app_commands.Range[str, 1, 256],
        priority: str,
        team: app_commands.Choice[str],
        officer: str,
        status: app_commands.Choice[str] = None,
        link: app_commands.Range[str, 1, 1024] = None,
        notes: app_commands.Range[str, 1, 1024] = None,
    ):
        task = task.strip()
        link = (link or "").strip()
        notes = (notes or "").strip()
        due_date = _parse_due_date(priority)
        officer_ids = _parse_officer_mentions(officer)

        problem = None
        if not task:
            problem = "The task description can't be blank."
        elif due_date is None:
            problem = "Priority must be a due date in MM/DD/YY format, such as 10/31/26."
        elif not officer_ids:
            problem = "Tag officers with @mentions picked from Discord's suggestions, such as @Thalia @Nicholas."
        elif link and not _is_web_link(link):
            problem = "Link must be a full URL starting with http:// or https://."
        if problem:
            await interaction.response.send_message(problem, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        async with self._sheets_lock:
            names = await asyncio.to_thread(self.sheets_client.get_officer_names)
        unknown = [i for i in officer_ids if i not in names]
        if unknown:
            await interaction.followup.send(
                "Not on the Officer List: " + ", ".join(f"<@{i}>" for i in unknown)
                + ". Add them with their Discord ID there, or tag a different officer.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        fields = {
            "Task": task,
            "Priority": due_date,
            "Team": team.value,
            "Officer": ", ".join(names[i] for i in officer_ids),
            "Status": status.value if status else DEFAULT_STATUS,
            "Link": link,
            "Notes": notes,
        }
        async with self._sheets_lock:
            row_number = await asyncio.to_thread(self.sheets_client.append_task, fields)

        embed = discord.Embed(title="Task added", description=f"**{task}**", color=discord.Color.green())
        embed.add_field(name="Due", value=due_date.strftime("%b %d, %Y"))
        embed.add_field(name="Team", value=fields["Team"])
        embed.add_field(name="Status", value=fields["Status"])
        embed.add_field(name="Officers", value=fields["Officer"], inline=False)
        if link:
            embed.add_field(name="Link", value=link, inline=False)
        if notes:
            embed.add_field(name="Notes", value=notes, inline=False)
        embed.set_footer(text=f"Added as row {row_number} of the Tasks tab. Only you can see this message.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @create_task.error
    async def create_task_error(self, interaction, error):
        await respond_error(interaction, error)

    # ---------- /complete-task ----------

    @app_commands.command(
        name="complete-task",
        description="Mark a task in the F26 Tracker as Completed.",
    )
    @app_commands.describe(task="Pick the task from the suggestions")
    @is_officer()
    async def complete_task(self, interaction: discord.Interaction, task: str):
        await interaction.response.defer(ephemeral=True, thinking=True)

        # Find the task in a fresh read rather than trusting the suggestion's
        # row number, and hold the lock until the write finishes.
        problem = None
        async with self._sheets_lock:
            self._suggestion_cache = None
            matches = _find_tasks(await asyncio.to_thread(self.sheets_client.get_task_rows), task)
            if not matches:
                problem = "No task in the tracker matches that. It may have been edited or removed; pick one from the suggestions."
            elif len(matches) > 1:
                problem = f"{len(matches)} tasks match that. Pick the one you mean from the suggestions, which show each task's row."
            else:
                row_number, row = matches[0]
                status = row.get('Status', '').strip()
                if status.lower() in DONE_STATUSES:
                    problem = f"**{row['Task'].strip()}** is already marked {status}."
                else:
                    await asyncio.to_thread(
                        self.sheets_client.set_task_status, row_number, task_key(row), COMPLETED_STATUS
                    )
        if problem:
            await interaction.followup.send(problem, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
            return

        embed = discord.Embed(title="Task completed", description=f"**{row['Task'].strip()}**", color=discord.Color.green())
        due_date = _parse_due_date(row.get('Priority'))
        if due_date:
            embed.add_field(name="Due", value=due_date.strftime("%b %d, %Y"))
        team = row.get('Team', '').strip()
        if team:
            embed.add_field(name="Team", value=team)
        officers = _split_officers(row.get('Officer'))
        if officers:
            embed.add_field(name="Officers", value=", ".join(officers), inline=False)
        embed.set_footer(text=f"Marked {COMPLETED_STATUS} in row {row_number} of the Tasks tab. Only you can see this message.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # Suggestions reveal tracker contents. Command checks don't cover
    # autocomplete, so the callback gets its own; failing it suggests nothing.
    @complete_task.autocomplete('task')
    @is_officer()
    async def complete_task_autocomplete(self, interaction: discord.Interaction, current: str):
        try:
            rows = await self._suggestion_rows()
        except SheetsError:
            return []

        pending = sorted(
            ((n, row) for n, row in rows if _row_is_pending(row)),
            key=lambda item: (_parse_due_date(item[1].get('Priority')) or date.max, item[0]),
        )
        wanted = current.strip().casefold()
        choices = []
        for row_number, row in pending:
            choice = _task_choice(row_number, row)
            if wanted in choice.name.casefold():
                choices.append(choice)
                if len(choices) == _CHOICE_LIMIT:
                    break
        return choices

    @complete_task.error
    async def complete_task_error(self, interaction, error):
        await respond_error(interaction, error)


async def setup(bot):
    await bot.add_cog(Reminders(bot))
