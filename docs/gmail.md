# Gmail authorization setup

Pebble exposes the Officer-only `/summarize-emails` command with `24h` and `7d`
choices. It is disabled by default and returns private, paginated embeds when
enabled. The authorization helper
checks mailbox profile metadata only; it neither fetches email bodies nor calls
Gemini. Run it locally without starting a second Discord bot instance.

## Create the OAuth client

For a regular Gmail account, enable Gmail API in Google Cloud Console, configure
an External audience, and add the club mailbox as a test user. Request only
`https://www.googleapis.com/auth/gmail.readonly`. Create a Desktop app client
under Google Auth Platform > Clients and download its JSON outside the repo.
The Sheets service-account key is a separate credential.

## Authorize locally

From the repository in PowerShell, substitute your download path:

```powershell
.\.venv\Scripts\python.exe gmail_auth.py --client-file "C:\Users\YOUR_USER\Downloads\pebble_gmail.json"
```

Sign in as the club Gmail account and grant read-only access. If Google displays
an unverified-app warning, confirm it identifies your own configured application
before proceeding. The helper uses a temporary local callback port and waits
up to five minutes. Add `--expected-account CLUB_ADDRESS@gmail.com` to refuse
saving a token for a different mailbox.

The helper prints the connected mailbox and saves access credentials to
`secrets/gmail_token.json`, which is ignored by Git and excluded from the Docker
image's application-copy steps. Keep the token private: it grants mailbox access.
The repository secret ignore rules do not replace operating-system access
controls or protect a token already committed to Git.

Check an existing token without opening a browser:

```powershell
.\.venv\Scripts\python.exe gmail_auth.py --check-only
```

## Testing versus production

Gmail authorization issued for an External OAuth app in Testing expires after
seven days. For durable deployment, move the application to Production when its
verification requirements or applicable exceptions have been established, then
rerun the authorization command with `--reauthorize`. Production does not itself
mean the application has been verified, and refresh tokens can still be revoked.

The base Compose file mounts only the Sheets key. Use the optional Gmail overlay
described below to mount a Gmail token when deploying live mode.

For real email summarization, select a Gemini project whose data-handling terms
are appropriate for the mailbox. Use synthetic data for unpaid-tier evaluation.

## Test Gemini with fictional emails

Set `GEMINI_API_KEY` in `.env`. Optionally set `GEMINI_MODEL`; the default is
`gemini-3.5-flash-lite`. Then run:

```powershell
.\.venv\Scripts\python.exe gemini_preview.py
```

The preview sends four hardcoded fictional conversations, including a request,
a resolved thread, an outgoing request awaiting an answer, and a newsletter.
It never reads the Gmail token or connects the Discord bot. Gemini returns an
overview, action items, possible unanswered requests, and requests awaiting
others. Returned references are checked against the supplied thread IDs;
facts and classification still need human review.

The adapter uses bounded requests and explicit quota errors. It does not
silently switch models, retry indefinitely, or truncate inputs. Large periods
are split into at most three bounded model requests by the Discord command.

## Enable the Discord command

The command has three modes, controlled by `EMAIL_SUMMARY_MODE`:

| Mode | Behavior |
| --- | --- |
| `disabled` | Default. Returns setup guidance and makes no Gmail or Gemini requests. |
| `demo` | Sends the four fictional conversations to Gemini. Both period choices use the same sample, explicitly labeled DEMO. Does not load the Gmail token. |
| `live` | Reads the connected Gmail mailbox and submits selected text to Gemini. Requires the token, expected account, and suitable API data-handling terms. |

For demo mode, set this in the deployment's `.env` and provide `GEMINI_API_KEY`:

```dotenv
EMAIL_SUMMARY_MODE=demo
GEMINI_MODEL=gemini-3.5-flash-lite
```

The key's billing state is not inferred from its value. The application does not
automatically upgrade the project or enforce billing. Configure live mode only
when the actual mailbox content is permitted under the API project's terms;
the unpaid-service terms prohibit sensitive, confidential, and personal data.
The free-key development path is the fictional demo.

For live mode, set:

```dotenv
EMAIL_SUMMARY_MODE=live
GMAIL_EXPECTED_ACCOUNT=your_club_address@gmail.com
GMAIL_TOKEN_FILE=secrets/gmail_token.json
```

The connected mailbox must match `GMAIL_EXPECTED_ACCOUNT` before conversations
are retrieved. Source links open that account in Gmail; the viewer still needs
their own browser access to it. Reply classifications are suggestions to verify,
not a guaranteed mailbox state.

## Selection and limits

- `24h` and `7d` are rolling time windows computed using UTC epoch seconds.
- Threads match any non-draft activity in the window; older replies are included
  for context. This is not a search of every historical unanswered email.
- At most 20 conversations are selected. Spam, trash, and drafts are excluded.
- Attachments are not downloaded or analyzed. Plain text is preferred; HTML is
  converted to visible text. Common quoted history is removed to reduce duplicates.
- Each conversation keeps up to 20 recent messages and 3,000 characters per body.
- Model requests stay under 40,000 serialized input characters, with at most
  three requests. Older context or excess conversations can be omitted.
- All detected shortening and omissions are shown in the private response.
- Only one summary runs at a time, with a one-minute cooldown after completion
  or failure. Google quota errors are surfaced rather than retried indefinitely.

No email body or digest is persisted by this feature or written to logs. There
is no daily schedule and no email send/modify permission.

## Deploy on the shared HARP droplet

Do not run a local Discord bot while the hosted instance is running. The local
authorization helper and fictional Gemini preview do not start Discord.

For demo mode, update/push the code, set the key and demo mode in the droplet's
`.env`, then rebuild from `/opt/pebble`:

```sh
git pull --ff-only
docker compose -p pebble up -d --build
docker compose -p pebble logs --tail=50 pebble
```

The automatically loaded `compose.override.yaml` preserves the droplet's Pebble
resource limits. Do not reinstall/restart Docker, reboot this shared host, or
change HARP's configuration to deploy the bot. Image building still shares host
resources, so deploy during a quiet period for registration traffic.

For live mode, securely copy the authorized token into
`/opt/pebble/secrets/gmail_token.json`. Make the file readable by container group
10001 and private to its owner/group, following the Sheets-key procedure.
Then include all three Compose files explicitly:

```sh
docker compose -p pebble -f compose.yaml -f compose.override.yaml -f compose.gmail.yaml up -d --build
docker compose -p pebble -f compose.yaml -f compose.override.yaml -f compose.gmail.yaml logs --tail=50 pebble
```

The overlay mounts the Gmail token read-only at `/run/secrets/gmail_token` and
overrides `GMAIL_TOKEN_FILE`. The runtime refreshes access tokens in memory; it
does not need to write the mounted file. Use the same file list for subsequent
live updates/recreates, or the Gmail mount will be removed. A dedicated host
without `compose.override.yaml` should omit that file from the list.

Test `/summarize-emails` in the club server with an Officer account. Every result
page is private; model-generated mentions are disabled. Confirm `/tasks` still
works and HARP remains healthy. Tests and Docker validation should pass before
deployment; local Python tests do not establish droplet runtime behavior.

Embedded HTTP/HTTPS URLs in message bodies are replaced with `[Link omitted]`
before truncation and model submission. This keeps login and tracking links out
of the model input; officers can open the original email through its Gmail link.
Optional invitations are summarized without automatically marking them as
awaiting a reply.

References: [Gmail Python setup](https://developers.google.com/workspace/gmail/api/quickstart/python),
[OAuth audience and Testing expiry](https://support.google.com/cloud/answer/15549945?hl=en),
[Gemini service terms](https://ai.google.dev/gemini-api/terms).
