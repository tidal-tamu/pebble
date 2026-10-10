# Pebble

Pebble is a Python Discord bot for club officers. It reads a Google Sheets task
tracker, displays pending tasks privately, sends officer reminders, and posts
announcements. Run one instance locally or in Docker on a server.

## Commands

All commands require the configured Officer role and run inside a Discord server.

| Command | Behavior |
| --- | --- |
| `/tasks` | Privately lists all pending tasks, sorted by due date. |
| `/my-tasks` | Privately lists pending tasks assigned to your Discord ID. |
| `/remind` | Posts one message per eligible task in the configured reminder channel and mentions its assigned officers. |
| `/announce` | Posts a titled announcement in a selected channel. The optional `ping_role` is literal mention text such as `<@&ROLE_ID>`. |
| `/summarize-emails` | Privately summarizes `24h` or `7d` conversations. Disabled by default; supports a fictional demo and explicitly configured live mode. |
| `/registration-stats` | Posts live Harp registration totals and counts by status in the channel. |

`/registration-stats` shows submitted applications prominently. Applications
started includes drafts; submitted includes every non-draft status. Set
`HARP_BOT_API_KEY` in the bot's server-side environment or untracked `.env`,
restart the bot, then use the command in the club server with the Officer role.

Reminders include overdue tasks, tasks due within seven days, and tasks without a
recognized due date. `done` and `completed` statuses are skipped, ignoring case.
Concurrent reminder sweeps are rejected, but running the command again after a
sweep finishes sends the reminders again. There is no automatic schedule.

## Local setup

Use **Python 3.13**, matching the Docker image and CI checks.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
Copy-Item .env.example .env
```

On Linux/macOS:

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
cp .env.example .env
```

Skip copying `.env.example` if `.env` already exists. Fill in `.env` and put the
Google service-account key at `service_account.json`. Follow
[the setup guide](docs/setup.md) for Discord permissions and spreadsheet access.
Then start the bot from the repository:

```powershell
.\.venv\Scripts\python.exe bot.py
```

On Linux/macOS, use `.venv/bin/python bot.py`. The log should show the extensions
loaded, commands registered, and Pebble connected. Stop the process with Ctrl+C.
The computer and process must stay running for the bot to remain online.

## Configuration

Settings are read once at startup. Restart after changing them. Environment
variables override `.env`, and relative credential paths resolve from the repo.

| Variable | Required | Purpose |
| --- | --- | --- |
| `DISCORD_TOKEN` | Yes | Bot token from the Discord Developer Portal. |
| `SPREADSHEET_ID` | Yes | Spreadsheet ID between `/d/` and `/edit`; not the full URL. |
| `OFFICER_ROLE_ID` | Yes | Role allowed to invoke the commands. |
| `REMINDER_CHANNEL_ID` | Yes | Channel receiving task reminders. |
| `GUILD_ID` | No | Server for immediate guild-scoped command registration. Leave blank for global registration. |
| `GOOGLE_CREDENTIALS_FILE` | No | Defaults to `service_account.json`. Compose overrides this with the mounted secret path. |
| `LOG_LEVEL` | No | `INFO` by default; also supports `DEBUG`, `WARNING`, `ERROR`, `CRITICAL`. |
| `EMAIL_SUMMARY_MODE` | No | `disabled` by default; `demo` uses fictional emails; `live` reads Gmail and submits text to Gemini. |
| `GEMINI_API_KEY` | For demo/live | Gemini API key, excluded from settings representations. |
| `GEMINI_MODEL` | No | Defaults to `gemini-3.5-flash-lite`. |
| `GMAIL_TOKEN_FILE` | For live | Saved OAuth token; defaults to `secrets/gmail_token.json`. |
| `GMAIL_EXPECTED_ACCOUNT` | For live | Mailbox identity that the saved Gmail token must match. |
| `HARP_BOT_API_KEY` | For registration stats | Private bearer key for Harp's registration integration API. Leave blank to keep the command unavailable. |

Tokens and credential keys are excluded from Git and the Docker build. Keep keys
outside tracked files; `.gitignore` cannot protect a secret that was already committed.

## Repository layout

```text
bot.py                 Startup, connection lifecycle, extension registration
config.py              Validated configuration
checks.py              Shared Officer-role access check
errors.py              Shared private command-error responses
sheets.py              Google Sheets adapter and officer name resolution
harp.py                Harp integration client and registration response parser
cogs/                  Discord feature modules
tests/                 Offline regression tests
docs/                  Setup, deployment, and architecture guides
compose.yaml           Single-instance Docker deployment
requirements.txt       Direct dependency versions
requirements.lock      Complete dependency snapshot used by setup, Docker, and CI
```

## Verification

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
```

Tests use mocked external services and send no Discord messages. CI runs them on
Python 3.13 and builds the container. For live checks, use a private test channel
and a separate tracker with one task assigned to a tester. Verify `/tasks`,
`/my-tasks`, `/announce`, and then `/remind`. Verify a member without the Officer
role is denied. Avoid reminder testing against a full tracker of real assignees.

## Deployment and future development

- [Discord and Google Sheets setup](docs/setup.md)
- [DigitalOcean deployment and operations](docs/deployment.md)
- [Architecture and adding integrations](docs/architecture.md)
- [Gmail authorization setup](docs/gmail.md)

Manual email summaries are available through `/summarize-emails`; see the Gmail
guide for mode selection and setup. Daily digests and reaction roles remain
planned. Local Gmail authorization and a Gemini preview
using fictional threads are also available.
The current bot uses Sheets as its source of truth and has no
local database. Add persistent storage when a feature needs saved mappings or
job history, rather than storing that state only in memory.
