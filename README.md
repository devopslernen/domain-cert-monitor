# domain-cert-monitor

A small self-hosted web app that watches a list of domains and warns you before
their TLS certificate or their domain registration expires.

## What it does

- You add and remove domains in a web UI.
- For each domain it reads the TLS certificate on port 443 and looks up the
  registration expiry date via RDAP (rdap.org).
- Each domain gets a traffic-light status:

  | Status | Meaning |
  |--------|---------|
  | green  | more than 30 days left |
  | yellow | 8 to 30 days left |
  | red    | 7 days or less (including already expired), a check error, or a certificate that fails verification |
  | grey   | domain expiry unknown, or not checked yet |

- All domains are checked at start, then every 6 hours, and whenever you click
  "Check now" or "Check all".
- It sends an email alert when an expiry date is 30, 14 or 7 days away, once per
  threshold.

The app runs as one Docker container. It stores its data in a SQLite database
on a Docker volume, and needs no external services except an SMTP server for
the alert emails.

> **No login.** The app has no authentication of its own. Run it on a trusted
> network or behind a reverse proxy that handles access control.

## Quick start

You need Docker with the Compose plugin.

```sh
git clone https://github.com/devopslernen/domain-cert-monitor.git
cd domain-cert-monitor
cp .env.example .env
docker compose up -d
```

Then open <http://localhost:8080> and add a domain.

Without SMTP settings the app runs normally but sends no alerts, and the UI
shows a notice. To enable alerts, set the `SMTP_*` variables and `ALERT_TO` in
`.env` (see [Configuration](#configuration)) and run `docker compose up -d`
again.

Useful commands:

```sh
docker compose logs -f     # follow the logs
docker compose down        # stop; the data stays in the volume
docker compose down -v     # stop and delete all data
```

### Try alerts with Mailpit

The compose file includes [Mailpit](https://mailpit.axllent.org/), a local mail
catcher, under the `dev` profile. Set this in `.env`:

```sh
SMTP_HOST=mailpit
SMTP_PORT=1025
SMTP_SECURITY=none
SMTP_FROM=monitor@example.com
ALERT_TO=you@example.com
```

Then start both containers and read the caught emails at
<http://localhost:8025>:

```sh
docker compose --profile dev up -d
```

### Released images

Each published release pushes an image to the GitHub Container Registry, tagged
with the release tag and `latest`:

```sh
docker pull ghcr.io/devopslernen/domain-cert-monitor:latest
```

## Configuration

All settings live in `.env`. Copy `.env.example` and adjust it; the defaults
work for a first start.

| Variable               | Default                 | Meaning |
|------------------------|-------------------------|---------|
| `PORT`                 | `8080`                  | HTTP port inside the container, and the published host port |
| `BASE_URL`             | `http://localhost:8080` | Public URL of the app, used in alert email links |
| `CHECK_INTERVAL_HOURS` | `6`                     | Hours between full check runs (a float, e.g. `0.01` for testing) |
| `ALERT_THRESHOLDS`     | `30,14,7`               | Days before expiry at which alerts are sent (comma-separated) |
| `SMTP_HOST`            | empty                   | SMTP server for alert emails; alerts are disabled if empty |
| `SMTP_PORT`            | `587`                   | SMTP port (587 for `starttls`, 465 for `tls`) |
| `SMTP_USER`            | empty                   | SMTP login user; leave empty to send without authentication |
| `SMTP_PASSWORD`        | empty                   | SMTP login password |
| `SMTP_SECURITY`        | `starttls`              | Connection security: `starttls`, `tls` or `none` |
| `SMTP_FROM`            | empty                   | Sender address of alert emails |
| `ALERT_TO`             | empty                   | Alert recipients (comma-separated) |
| `TZ`                   | `UTC`                   | Display time zone (IANA name, e.g. `Europe/Berlin`) |
| `LOG_LEVEL`            | `info`                  | `debug`, `info`, `warning` or `error` |

Alerts are sent only when `SMTP_HOST`, `SMTP_FROM` and `ALERT_TO` are all set.
`ALERT_THRESHOLDS` only controls which alerts fire; the status colours above
are fixed.

## More

- [SPEC.md](SPEC.md): what the app does, in detail
- [ARCHITECTURE.md](ARCHITECTURE.md): how it is built, including the JSON API
  (also browsable at `/api/docs` in the running app)
