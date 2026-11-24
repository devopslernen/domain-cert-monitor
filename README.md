# domain-cert-monitor

A small self-hosted web app that watches a list of domains and warns you before
their TLS certificate or their domain registration expires.

- Add and remove domains in a web UI.
- For each domain, it reads the TLS certificate on port 443 and looks up the
  registration expiry date via RDAP (rdap.org).
- Each domain gets a traffic-light status: green (more than 30 days left),
  yellow (8–30 days), red (7 days or less, expired, or the check failed).
  A registration expiry that RDAP doesn't provide is shown as "unknown".
- All domains are checked every 6 hours, at start, and on demand
  ("Check now" / "Check all").
- Email alerts go out when an expiry date is 30, 14 or 7 days away, once per
  threshold.

It runs as one Docker container with a SQLite database on a named volume. The
only external service it needs is an SMTP server for the alert emails.

> **No authentication.** The app has no login. Run it on a trusted network or
> behind a reverse proxy that handles access control.

## Quick start

You need Docker with the Compose plugin.

```sh
git clone https://github.com/devopslernen/domain-cert-monitor.git
cd domain-cert-monitor
cp .env.example .env
docker compose up -d
```

Open <http://localhost:8080> and add a domain. It is checked immediately.

Without SMTP settings the app runs normally but sends no alerts, and the UI
shows a notice. To get alerts, set the `SMTP_*` and `ALERT_TO` variables in
`.env` (see below) and run `docker compose up -d` again.

Useful commands:

```sh
docker compose logs -f     # logs (stdout)
docker compose down        # stop; the data volume is kept
docker compose down -v     # stop and delete all data
```

### Trying out alerts with Mailpit

The compose file includes an optional [Mailpit](https://mailpit.axllent.org/)
mail catcher. Set this in `.env`:

```sh
SMTP_HOST=mailpit
SMTP_PORT=1025
SMTP_SECURITY=none
SMTP_FROM=monitor@example.com
ALERT_TO=you@example.com
```

Then start both containers with `docker compose --profile dev up -d` and read
the caught emails at <http://localhost:8025>.

### Pre-built image

Each release publishes an image to
`ghcr.io/devopslernen/domain-cert-monitor`, tagged with the release tag and
`latest`. To use it instead of building locally, replace `build: .` and
`image:` in `docker-compose.yml` with:

```yaml
    image: ghcr.io/devopslernen/domain-cert-monitor:latest
```

## Configuration

All settings are environment variables, read from `.env`. Copy `.env.example`
and adjust it; restart the container after a change.

| Variable               | Default                 | Meaning |
|------------------------|-------------------------|---------|
| `PORT`                 | `8080`                  | HTTP port inside the container, also the published host port |
| `BASE_URL`             | `http://localhost:8080` | Public URL of the app, used in alert email links |
| `CHECK_INTERVAL_HOURS` | `6`                     | Hours between full check runs (a float, e.g. `0.01` for testing) |
| `ALERT_THRESHOLDS`     | `30,14,7`               | Days before expiry at which alerts are sent (comma-separated) |
| `SMTP_HOST`            | *(empty)*               | SMTP server for alert emails; alerts are disabled if empty |
| `SMTP_PORT`            | `587`                   | SMTP port (587 for `starttls`, 465 for `tls`) |
| `SMTP_USER`            | *(empty)*               | SMTP login user; leave empty to send without authentication |
| `SMTP_PASSWORD`        | *(empty)*               | SMTP login password |
| `SMTP_SECURITY`        | `starttls`              | Connection security: `starttls`, `tls` or `none` |
| `SMTP_FROM`            | *(empty)*               | Sender address of alert emails |
| `ALERT_TO`             | *(empty)*               | Alert recipients (comma-separated) |
| `TZ`                   | `UTC`                   | Display time zone (IANA name, e.g. `Europe/Berlin`) |
| `LOG_LEVEL`            | `info`                  | `debug`, `info`, `warning` or `error` |

Alerts are sent only when `SMTP_HOST`, `SMTP_FROM` and `ALERT_TO` are all set.
`ALERT_THRESHOLDS` changes when emails are sent, not the status colours.

## More

- [SPEC.md](SPEC.md) — what the app does, in detail
- [ARCHITECTURE.md](ARCHITECTURE.md) — how it is built, including the JSON API
  (interactive docs at `/api/docs`)

Run the tests with `pip install -e '.[dev]' && pytest`.
