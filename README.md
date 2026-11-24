# domain-cert-monitor

A small self-hosted web app that watches a list of domains and warns you before
their TLS certificate or their domain registration expires.

## What it does

- You add and remove domains in a web UI.
- For each domain it reads the TLS certificate on port 443 and records the
  expiry date. An expired or otherwise invalid certificate still shows its real
  date, with a note saying what is wrong.
- It looks up the registration expiry date of the registrable domain through
  RDAP (rdap.org). Registries that publish no expiry date show "unknown".
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
  threshold. A renewal starts a fresh set of alerts.

The app has no login. Run it on a trusted network or behind a reverse proxy
that handles authentication.

## Quick start

You need Docker with the Compose plugin.

```sh
git clone https://github.com/devopslernen/domain-cert-monitor.git
cd domain-cert-monitor
cp .env.example .env
docker compose up -d
```

Open <http://localhost:8080> and add a domain. The API documentation is at
<http://localhost:8080/api/docs>.

The data lives in the named volume `data` (an SQLite database), so it survives
`docker compose down` and `up`. Only `docker compose down -v` deletes it.

Useful commands:

```sh
docker compose logs -f monitor   # follow the logs
docker compose up -d --build     # rebuild after an update
docker compose down              # stop (keeps the data)
```

### Released images

Each release is also published as an image on GitHub Container Registry:

```sh
docker pull ghcr.io/devopslernen/domain-cert-monitor:latest
```

To use it instead of a local build, replace `build: .` and `image: ...` in
`docker-compose.yml` with `image: ghcr.io/devopslernen/domain-cert-monitor:latest`
(or a release tag).

## Configuration

All settings are in `.env`. Edit the file, then run `docker compose up -d` again
to apply the changes.

| Variable | Default | Meaning |
|----------|---------|---------|
| `PORT` | `8080` | HTTP port inside the container, and the published host port |
| `BASE_URL` | `http://localhost:8080` | Public URL of the app, used in alert email links |
| `CHECK_INTERVAL_HOURS` | `6` | Hours between full check runs (a float, e.g. `0.01` for testing) |
| `ALERT_THRESHOLDS` | `30,14,7` | Days before expiry at which alerts are sent |
| `SMTP_HOST` | empty | SMTP server for alert emails. Alerts are disabled if empty. |
| `SMTP_PORT` | `587` | SMTP port (587 for `starttls`, 465 for `tls`) |
| `SMTP_USER` | empty | SMTP login user. Leave empty to send without authentication. |
| `SMTP_PASSWORD` | empty | SMTP login password |
| `SMTP_SECURITY` | `starttls` | Connection security: `starttls`, `tls` or `none` |
| `SMTP_FROM` | empty | Sender address of alert emails |
| `ALERT_TO` | empty | Alert recipients, comma-separated |
| `TZ` | `UTC` | Display time zone (IANA name, e.g. `Europe/Berlin`) |
| `LOG_LEVEL` | `info` | `debug`, `info`, `warning` or `error` |

Alerts are only sent when `SMTP_HOST`, `SMTP_FROM` and `ALERT_TO` are all set.
Until then the UI shows a notice. `ALERT_THRESHOLDS` changes when emails are
sent, not the status colours.

### Trying out alerts with Mailpit

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

## Development

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest              # unit and integration tests
pytest -m network   # tests that need internet access
```

[SPEC.md](SPEC.md) describes what the app does, and
[ARCHITECTURE.md](ARCHITECTURE.md) describes how it is built.
