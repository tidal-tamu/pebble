# DigitalOcean deployment and operations

## Deployment model

Run one Docker Compose service on a Linux DigitalOcean droplet. Keep only one
Pebble instance running for a given token: multiple instances can receive the
same events and perform duplicate work. Stop the local bot before starting the
hosted one.

The bot initiates outbound connections to Discord and Google APIs. The current
deployment publishes no inbound application ports and needs no domain or TLS
certificate. Keep SSH access limited to operators and enable the host firewall.
Gmail OAuth setup and any future webhook receiver should be designed separately.

Install Docker Engine and the Compose plugin using the
[official Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/).
The checked-in Dockerfile uses Python 3.13 and runs the bot as an unprivileged
user. The build copies only application code and dependencies. Credentials are
provided at runtime.

## First deployment

1. Clone this repository on the droplet and choose a tested commit.
2. Copy `.env.example` to `.env` and configure it using the setup guide.
3. Transfer the service-account key through a secure channel and save it as
   `service_account.json` beside `compose.yaml`.
4. Protect `.env` with owner-only access. Ensure the container user (UID 10001)
   can read the credential file through the Compose secret mount. For example,
   on Linux, give the key group 10001 with mode 640 while keeping its containing
   deployment directory accessible only to trusted operators. Compose file
   secrets are bind mounts; their permissions must work on the host.
5. Ensure Docker starts on boot. Stop any existing local Pebble process.
6. From the repository, start the service:

```sh
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 pebble
```

Look for both extensions loaded, commands registered, and a connected message.
Use `/tasks` in the test channel to confirm the live Sheets integration. After
deployment, reboot the droplet once and repeat the connection and task checks.

Compose mounts `service_account.json` at `/run/secrets/google_credentials` and
overrides `GOOGLE_CREDENTIALS_FILE` accordingly. If you use a different host
credential filename, update the secret's `file` setting in Compose.

## Routine operation

```sh
docker compose logs -f --tail=100 pebble
docker compose restart pebble
docker compose stop pebble
docker compose up -d pebble
```

`restart: unless-stopped` recovers from process exits and host/Docker restarts,
except when the service was deliberately stopped. Logs are rotated to three
10 MB files. A running container alone does not prove Discord and Sheets are
healthy; verify connection logs and occasionally use a private task command.

If environment variables or credentials change, recreate the container:

```sh
docker compose up -d --force-recreate pebble
```

Do not paste `docker compose config` output into a ticket or chat: it can expand
secret environment values. Keep logs and credentials out of public artifacts.

## Updates and rollback

Record the current Git commit before updating, and deploy only a revision that
passes CI. Keep a clean deployment checkout so updates don't overwrite local
code edits. The credentials and `.env` remain untracked.

```sh
git rev-parse HEAD
git pull --ff-only
docker compose up -d --build
docker compose logs --tail=100 pebble
```

If the new version fails, check out the recorded previous commit and rebuild:

```sh
git checkout PREVIOUS_COMMIT
docker compose up -d --build
```

Verify `/tasks` after either an update or rollback. Remember that this rollback
changes code, not externally registered commands or spreadsheet data. Future
database migrations will need their own backup and rollback procedure.

## Backups and future storage

The current bot has no durable local application state; its tasks and officer
roster live in Sheets. Keep credentials in an approved secret store or protected
backup, and keep account ownership accessible to future club officers. Never
commit keys to make deployment easier.

Reaction-role mappings, email progress, and job history will need durable
storage. For a single bot, SQLite on a persistent volume is a reasonable next
step. Add backup and restore instructions when that storage is implemented.

## Verification limits

Local Python tests cover command logic and integration failures. Docker needs
an actual build and runtime check on a Docker-capable host. Reboot recovery,
host permissions, and droplet networking must be verified on the deployment
host; repository checks cannot establish those properties.
