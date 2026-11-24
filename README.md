# domain-cert-monitor

A small self-hosted web app that watches a list of domains and warns you before
their TLS certificate or their domain registration expires.

## What it does

- You add and remove domains in a web UI.
- For each domain it reads the TLS certificate on port 443 and looks up the
  registration expiry date via RDAP (rdap.org). If the registry publishes no
  expiry date, the domain column shows "unknown".
- Each domain gets a traffic-light status:

  | Status | Meaning |
  |--------|---------|
  | green (OK) | more than 30 days left |
  | yellow (Soon) | 8 to 30 days left |
  | red (Critical / Error) | 7 days or less, already expired, the check failed, or the certificate fails verification |
  | grey (Unknown) | domain expiry unknown, or not checked yet |

- All domains are checked at start, then every 6 hours, and whenever you click
  "Check now" or "Check all".
- It sends an email when an expiry date is 30, 14 or 7 days away, once per
  threshold. A renewed certificate or domain starts a fresh set of alerts.

The app has **no login**. Run it on a trusted network or behind a reverse proxy
that handles authentication.

## Quick start

You need Docker with the Compose plugin.

```sh
git clone https://github.com/devopslernen/domain-cert-monitor.git
cd domain-cert-monitor
cp .env.example .env
docker compose up -d
```

Open <http://localhost:8080> and add a domain. The app works without further
settings. To receive alert emails, set the SMTP variables in `.env` (see
[Configuration](#configuration)) and run `docker compose up -d` again.

Useful commands:

```sh
docker compose logs -f monitor   # logs
docker compose down              # stop; the data is kept
docker compose down -v           # stop and delete the data
```

The domains and alert records are stored in a SQLite database on the named
volume `data`.

### Released images

Each published release is also pushed to the GitHub Container Registry, tagged
with the release tag and `latest`:

```sh
docker pull ghcr.io/devopslernen/domain-cert-monitor:latest
```

### Try out alerts with Mailpit

The compose file includes [Mailpit](https://mailpit.axllent.org/), a mail
catcher for testing. Set this in `.env`:

```sh
SMTP_HOST=mailpit
SMTP_PORT=1025
SMTP_SECURITY=none
SMTP_FROM=monitor@example.com
ALERT_TO=you@example.com
```

Then start both containers and read the caught emails at <http://localhost:8025>:

```sh
docker compose --profile dev up -d
```

## Configuration

All settings are environment variables in `.env`. Restart the container after a
change (`docker compose up -d`).

| Variable | Default | Meaning |
|----------|---------|---------|
| `PORT` | `8080` | HTTP port inside the container, and the published host port |
| `BASE_URL` | `http://localhost:8080` | Public URL of the app, used in alert email links |
| `CHECK_INTERVAL_HOURS` | `6` | Hours between full check runs (a float, e.g. `0.01` for testing) |
| `ALERT_THRESHOLDS` | `30,14,7` | Days before expiry at which alerts are sent (comma-separated). The status colours don't change with this setting. |
| `SMTP_HOST` | empty | SMTP server for alert emails |
| `SMTP_PORT` | `587` | SMTP port (587 for `starttls`, 465 for `tls`) |
| `SMTP_USER` | empty | SMTP login user; leave empty to send without authentication |
| `SMTP_PASSWORD` | empty | SMTP login password |
| `SMTP_SECURITY` | `starttls` | Connection security: `starttls`, `tls` or `none` |
| `SMTP_FROM` | empty | Sender address of alert emails |
| `ALERT_TO` | empty | Alert recipients (comma-separated) |
| `TZ` | `UTC` | Display time zone (IANA name, e.g. `Europe/Berlin`) |
| `LOG_LEVEL` | `info` | Log level: `debug`, `info`, `warning`, `error` |

Alerts are sent only when `SMTP_HOST`, `SMTP_FROM` and `ALERT_TO` are all set.
Otherwise the UI shows a notice and alerts are skipped.

## API

The UI uses a JSON API under `/api`. Interactive documentation is at
`/api/docs`, and `GET /healthz` returns `{"status":"ok"}` when the app and the
database are up.

## Development

```sh
pip install -e ".[dev]"
pytest                # unit and integration tests
pytest -m network     # tests that need internet access
```

See [SPEC.md](SPEC.md) for the requirements and
[ARCHITECTURE.md](ARCHITECTURE.md) for the design.
