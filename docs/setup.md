# Discord and Google Sheets setup

## Discord application

Reuse the existing Pebble application when available, or create an application
in the [Discord Developer Portal](https://discord.com/developers/applications).

On its Bot page:

1. Keep Public Bot off for an owner-installed club bot.
2. Keep Requires OAuth2 Code Grant off.
3. Enable Server Members Intent and Message Content Intent. The current code
   requests both; Presence Intent is not needed.
4. Generate/reset the bot token and put it directly into `DISCORD_TOKEN` in `.env`.
   Resetting the token invalidates the old one.

Under OAuth2 > URL Generator, select `bot` and `applications.commands`. Select
View Channels, Send Messages, and Embed Links, then open the generated invite
URL and authorize the bot in the club server. This requires permission to manage
the server. The permissions calculator on the Bot page does not itself change
server permissions.

Ensure the test/reminder channel also grants Pebble View Channel, Send Messages,
and Embed Links. Private channel overrides can deny access even when the bot's
server role has those permissions. Announcements need the same permissions in
their destination channel. Optional `@everyone`/`@here` or non-mentionable role
pings require the appropriate Mention Everyone permission; regular reminders
explicitly disallow role and everyone mentions.

Enable Developer Mode under Discord's User Settings > Advanced, then copy:

- Server ID into `GUILD_ID`.
- Reminder/test channel ID into `REMINDER_CHANNEL_ID`.
- Officer role ID from Server Settings > Roles into `OFFICER_ROLE_ID`.

Give testers the Officer role. Administrator permission alone does not bypass
Pebble's Officer-role check.

## Google service account

1. Select/create a project in [Google Cloud Console](https://console.cloud.google.com/).
2. Enable Google Sheets API and Google Drive API.
3. Under IAM & Admin > Service Accounts, create a service account. No project IAM
   role is needed to read a spreadsheet shared directly with that account.
4. Open the account's Keys tab and create a JSON key.
5. Save it locally as `service_account.json` or configure `GOOGLE_CREDENTIALS_FILE`.
6. Share the tracker with the key's `client_email` address as Viewer.
7. Set `SPREADSHEET_ID` to the ID in the tracker's URL, between `/d/` and `/edit`.

The current integration requests read-only scopes and never changes the tracker.
Workspace restrictions may require an administrator to allow sharing with the
service account. Do not make the spreadsheet public as a workaround.

## Tracker schema

Tab names must be exactly `Tasks` and `Officer List`.

The `Tasks` header row uses these case-sensitive names:

| Column | Meaning |
| --- | --- |
| `Task` | Task description. Blank descriptions are ignored. |
| `Priority` | Due date, despite the legacy column name. |
| `Team` | Optional team displayed in messages. |
| `Officer` | Assignees separated by commas, semicolons, or newlines. |
| `Status` | `done`/`completed` are complete; everything else is pending. |
| `Link` | Optional supporting URL. |

Supported dates: `MM/DD/YYYY`, `MM/DD/YY`, `YYYY-MM-DD`, and `MM-DD-YYYY`.
Blank or unrecognized due dates remain eligible for reminders. Date comparisons
currently use the process's local date; Docker defaults to UTC. Introduce an
explicit application timezone before adding scheduled jobs.

The `Officer List` header row uses `Name` and `Discord ID`. Names are matched
case-insensitively. Header aliases `Officer`/`Officer Name` and
`DiscordID`/`Discord` are accepted, ignoring header case and surrounding spaces.
Store Discord IDs as plain text in Sheets to preserve their digits; precision
lost by spreadsheet number formatting cannot be recovered by the bot.

Full names resolve directly. First names also resolve when unique across the
roster. Ambiguous first names require full names in `Tasks`. A person with
multiple matching aliases still sees each task only once in `/my-tasks`.

## Common problems

| Symptom | Check |
| --- | --- |
| Token rejected | Reset/copy the bot token, not the application's client secret. Restart after updating `.env`. |
| Privileged intent error | Enable Members and Message Content intents on the Bot page. |
| Commands absent | Check invitation scopes, the configured guild, and command registration logs. Global commands may take time to appear. |
| Officer check denied | The caller needs the exact role configured by ID, in the server where they invoke the command. |
| Spreadsheet inaccessible | Confirm the ID, service-account sharing, enabled APIs, and tab names. |
| Tasks cannot be read | Check for duplicate/incorrect headers in the first row. |
| Own tasks missing | Match the assignee name to the roster and verify the stored Discord ID. |
| Reminder channel inaccessible | Check channel ID, server membership, and channel permission overrides. |
| Voice-library warnings | Pebble has no voice feature; those optional dependencies are not required. |

For more detail, see the [discord.py setup guide](https://discordpy.readthedocs.io/en/stable/discord.html)
and [gspread service-account guide](https://docs.gspread.org/en/latest/oauth2.html).
