# Architecture and future integrations

## Current boundaries

Pebble is one Python process with independently loaded Discord cogs. This is
appropriate for a club bot and gives each feature a clear home.

| Module | Responsibility |
| --- | --- |
| `bot.py` | Discord lifecycle, extension loading, slash-command registration. |
| `config.py` | Configuration loading and validation. Token values are excluded from settings representations. |
| `checks.py` | Officer-role authorization. Missing settings and direct messages fail closed. |
| `errors.py` | Private error responses before/after interaction deferral. Raw API responses are not shown to users. |
| `sheets.py` | Synchronous read-only Google API calls and officer lookup. |
| `cogs/reminders.py` | Task filtering, task views, reminder formatting/delivery. |
| `cogs/announcements.py` | Announcement input validation and delivery. |

Settings are loaded once at startup and passed through `bot.settings`. Required
values fail validation before login. Credential parsing happens when the
reminder cog loads; a failed extension aborts startup rather than silently
leaving commands missing. Actual spreadsheet access is checked on demand.

Google Sheets uses blocking HTTP with connect/read timeouts. The reminder cog
runs those calls through `asyncio.to_thread` and serializes access to the shared
Google client with an asynchronous lock. Discord remains responsive while
requests run. A separate lock rejects simultaneous `/remind` invocations.
These locks are process-local and do not coordinate multiple bot instances.

API failures raise a readable `SheetsError`; an empty sheet remains an empty
result. Command handlers defer their responses before external work and use
private replies. Reminder delivery stops on an error and reports partial progress.
Sequential sweeps can still duplicate reminders; durable deduplication is a
future feature.

## Adding a feature

1. Add a cog under `cogs/` for Discord commands and events.
2. Put an external API adapter in its own module when needed. Keep API request
   and authentication details out of command callbacks.
3. Add configuration fields to `Settings`, validate them, and document them in
   `.env.example` and the setup guide. An optional integration should validate
   its credentials only when enabled.
4. Add the cog to `EXTENSIONS` in `bot.py`.
5. Apply `is_officer()` to management commands. User-facing reaction-role events
   need a different, explicit eligibility policy.
6. Set timeouts, handle failures privately, and avoid logging secrets or raw
   email content. Use asynchronous HTTP clients or offload blocking libraries.
7. Test normal behavior, authentication failures, retries, and restarts with
   mocked services before testing against a controlled live environment.

Avoid reading configuration independently in each cog. Shared services can be
owned by the bot once multiple features need them. Move task parsing into a
separate domain module when a second consumer needs it; the current small cog
does not require an application framework.

## Planned features

| Feature | Initial delivery | Additional requirements |
| --- | --- | --- |
| Reaction roles | Officer-created message with emoji/role mappings | Persist mappings; handle uncached messages; decide removal behavior; enforce role hierarchy and assignable-role limits. |
| Registration counts | Officer command fetching aggregate counts | Authenticated API contract, timeout/retry policy, definition of count, and freshness timestamp. |
| Gmail + Gemini digest | Manual private preview before daily delivery | Account authorization, email selection, allowed destination, content-handling policy, model/cost choice, scheduling, and durable progress. |

Before scheduling anything, add an explicit timezone and decide how jobs recover
after downtime. Record completed runs in persistent storage and define retry
semantics. Sending a Discord message and recording its completion are not one
atomic operation, so exactly-once delivery needs a deliberate design.

For a single droplet, SQLite with a mounted data directory is a suitable starting
point when persisted state is needed. Consider a shared database and distributed
job coordination only if multiple processes become a real requirement.

## Dependencies and checks

`requirements.txt` records direct versions; `requirements.lock` records the full
installed dependency set. To update, create a clean Python 3.13 environment,
install the direct dependencies, regenerate the lock with `python -m pip freeze`,
and run tests, dependency checks, and a container build. Commit both files when
direct versions change. The current lock is a version snapshot, not a
hash-verified package lock.

CI installs the lock on Linux/Python 3.13, checks dependency consistency, runs
the offline tests, and builds the Docker image. Tests must not depend on `.env`,
the real credential key, live Discord, or live Google Sheets.
