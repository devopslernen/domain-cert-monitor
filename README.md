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
  | green  | more than 30 days left |
  | yellow | 8 to 30 days left |
  | red    | 7 days or less (including already expired), a check error, or a certificate that fails verification |
  | grey   | domain expiry unknown, or not checked yet |

- All domains are checked at start, then every 6 hours, and whenever you click
  "Check now" or "Check all".
- It sends an email when a certificate or registration is 30, 14 or 7 days from
  expiry, once per threshold. A renewal starts a fresh set of alerts.

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

Open <http://localhost:8080> and add a domain. The first check starts at once.

The domains and alert records live in the named volume `data`. They survive
`docker compose down`; only `docker compose down -v` removes them.

Logs go to stdout: `docker compose logs -f monitor`.

### Released images

Each GitHub release publishes an image to
`ghcr.io/devopslernen/domain-cert-monitor`, tagged with the release tag and
`latest`. To run it instead of building locally, replace `build: .` and
`image:` in `docker-compose.yml` with:

```yaml
    image: ghcr.io/devopslernen/domain-cert-monitor:latest
```

### Trying out alerts

The compose file includes [Mailpit](https://mailpit.axllent.org/), a mail
catcher, in the `dev` profile. Set this in `.env`:

```sh
SMTP_HOST=mailpit
SMTP_PORT=1025
SMTP_SECURITY=none
SMTP_FROM=monitor@example.com
ALERT_TO=you@example.com
```

Then start both services and read the caught mail at <http://localhost:8025>:

```sh
docker compose --profile dev up -d
```

## Configuration

All settings are in `.env`. Restart the container after a change
(`docker compose up -d`).

| Variable               | Default                 | Meaning |
|------------------------|-------------------------|---------|
| `PORT`                 | `8080`                  | HTTP port inside the container, and the published host port |
| `BASE_URL`             | `http://localhost:8080` | Public URL of the app, used in alert email links |
| `CHECK_INTERVAL_HOURS` | `6`                     | Hours between full check runs (a float, e.g. `0.01` for testing) |
| `ALERT_THRESHOLDS`     | `30,14,7`               | Days before expiry at which alerts are sent (comma-separated) |
| `SMTP_HOST`            | empty                   | SMTP server for alert emails |
| `SMTP_PORT`            | `587`                   | SMTP port (587 for `starttls`, 465 for `tls`) |
| `SMTP_USER`            | empty                   | SMTP login user; leave empty to send without authentication |
| `SMTP_PASSWORD`        | empty                   | SMTP login password |
| `SMTP_SECURITY`        | `starttls`              | Connection security: `starttls`, `tls` or `none` |
| `SMTP_FROM`            | empty                   | Sender address of alert emails |
| `ALERT_TO`             | empty                   | Alert recipients (comma-separated) |
| `TZ`                   | `UTC`                   | Display time zone (IANA name, e.g. `Europe/Berlin`) |
| `LOG_LEVEL`            | `info`                  | `debug`, `info`, `warning` or `error` |

Alerts are sent only when `SMTP_HOST`, `SMTP_FROM` and `ALERT_TO` are all set.
Otherwise the UI shows a notice and no email goes out.

`ALERT_THRESHOLDS` only controls which emails are sent. The status colours
always use the fixed bands in the table above.

## Development

```sh
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest              # unit and integration tests
.venv/bin/pytest -m network   # tests that need internet access
```

The JSON API is documented at `/api/docs` in the running app. See
[SPEC.md](SPEC.md) for the requirements and [ARCHITECTURE.md](ARCHITECTURE.md)
for the design.
