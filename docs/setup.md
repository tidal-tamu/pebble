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

To enable `/assign-role`, copy the Judge, Mentor, and Hacker role IDs into
`JUDGE_ROLE_ID`, `MENTOR_ROLE_ID`, and `HACKER_ROLE_ID`, then restart Pebble.
Grant the bot Manage Roles and place its highest role above those three roles.
The command accepts only those participant roles and requires the Officer role.

## Hacker reaction roles

1. Create an unmanaged Hacker role with **all server permission switches off**.
   It must not be the Officer role. Remove any channel/category overrides that
   grant permissions to Hacker; deny-only overrides are allowed. Other roles
   and `@everyone` can still give members their normal server access.
2. Put its ID in `HACKER_ROLE_ID` and restart Pebble.
3. Give Pebble Manage Roles and put its highest role above Hacker. In the panel
   channel, grant Pebble View Channel, Read Message History, and Add Reactions,
   plus Send Messages if it will create a message. Members need View Channel,
   Read Message History, and access to react there. Custom emoji must be usable
   by Pebble and the members.
4. As an Officer, run `/reaction-role channel:#roles emoji:💻`. Pebble creates
   the message and adds the initial reaction. To use an existing message,
   include `message_id:MESSAGE_ID` from the selected channel. It can have been
   posted before Pebble started, including by someone else. For an existing
   message, make sure its text explains that removing the reaction removes Hacker.
5. Test with a member who lacks Hacker: react to gain the role, then remove the
   reaction to lose it. Other emoji and messages are ignored. Normal reactions
   and super reactions both count; the role stays while either exists.

There is one active message/emoji mapping per server. Running the command again
replaces it. Selections follow the new message: previously tracked members who
have not reacted there lose Hacker. Existing reactions on the new message count
immediately. Do not change `HACKER_ROLE_ID` for an active panel: Pebble stops
processing that panel if the configured ID changes, rather than silently
granting or removing a different role.

The only permitted self-selection role is `HACKER_ROLE_ID`. There is no role
input on this command. Pebble fetches current role permissions and channel
overrides before syncing and again before each grant. A permission change,
managed role, deleted role, or insufficient bot hierarchy blocks processing and
produces an actionable error log. If Hacker later acquires permissions, remove
them before processing can resume; review any existing holders separately.

Configuration and tracked selections are saved in `REACTION_ROLES_DB`, defaulting
to `data/reaction_roles.sqlite3`. Its directory must be writable by Pebble.
Compose mounts the `pebble_data` named volume at `/app/data`; keep that volume
when rebuilding or recreating the container. Do not use `docker compose down -v`
if you want to preserve selections. Back up the database while the bot is stopped.

Raw reaction events work without Discord's message cache. At startup, on gateway
resume, and every five minutes, Pebble reconciles current reactions with tracked
selections. This recovers offline additions/removals and retries transient role
failures. Removing all reactions also removes tracked Hacker roles. Members who
were never tracked by the panel keep manually assigned Hacker roles. Once a
member uses the panel, their reaction controls that role, including an existing
Hacker role they already held. A deleted/inaccessible panel logs an error and
preserves roles until an Officer configures an accessible message.

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
