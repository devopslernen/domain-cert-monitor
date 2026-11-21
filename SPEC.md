# domain-cert-monitor — Specification

Status: draft · Date: 2026-11-21

## Goal

A small self-hosted web app that watches a list of domains and warns before
their TLS certificate or their domain registration expires. One user (or one
small team) on a trusted network. It runs as one Docker container and needs no
external services except the SMTP server used for alert emails.

## Features

1. Add and remove domains in a web UI.
2. For each domain, read the TLS certificate on port 443 and record its expiry date.
3. For each domain, look up the registration expiry date via RDAP (rdap.org).
   If no expiry date can be found, show "unknown".
4. Give each domain a traffic-light status (green / yellow / red).
5. Check all domains every 6 hours, and when the user asks (one domain or all).
6. Send email alerts when an expiry date is 30, 14 or 7 days away, once per threshold.
7. Keep all data in a Docker volume and all settings in `.env`.

## Out of scope

- User accounts, login, roles. The app has no authentication of its own and
  is meant to run behind a trusted network or reverse proxy (see Open questions).
- Ports other than 443, STARTTLS protocols (SMTP, IMAP, …), client certificates.
- Full certificate validation reports (cipher suites, protocol versions, OCSP,
  CT logs, chain grading). Only expiry and handshake errors count.
- WHOIS (port 43) as a fallback for RDAP.
- Alert channels other than email (Slack, webhooks, push).
- Historical charts and check history beyond the latest result.
- Multiple instances, high availability, external databases.
- Renewing certificates or domains.

## Data model

Storage: SQLite file at `/data/monitor.db` inside the container, on a named volume.

### `domains`

| Field          | Type      | Notes |
|----------------|-----------|-------|
| `id`           | integer   | primary key |
| `hostname`     | text      | unique, lower-case, ASCII (IDNs stored as punycode); no scheme, path or port |
| `created_at`   | timestamp | UTC |

### `checks` (latest result per domain and kind; one row each)

| Field          | Type      | Notes |
|----------------|-----------|-------|
| `domain_id`    | integer   | FK → `domains.id`, delete cascades |
| `kind`         | text      | `cert` or `domain` |
| `checked_at`   | timestamp | UTC, time of the last attempt |
| `expires_at`   | timestamp | UTC, null if unknown or error |
| `error`        | text      | null on success; short message otherwise |
| `details`      | text/JSON | cert: subject, issuer, SANs, `verified` flag; domain: RDAP server used, registrable domain |

Primary key: (`domain_id`, `kind`).

### `alerts_sent`

| Field          | Type      | Notes |
|----------------|-----------|-------|
| `domain_id`    | integer   | FK → `domains.id`, delete cascades |
| `kind`         | text      | `cert` or `domain` |
| `threshold`    | integer   | 30, 14 or 7 |
| `expires_at`   | timestamp | the expiry date this alert was about |
| `sent_at`      | timestamp | UTC |

Unique key: (`domain_id`, `kind`, `threshold`, `expires_at`). Tying the row to
`expires_at` means a renewed certificate or domain (new expiry date) starts a
fresh set of alerts.

Derived (not stored): `days_left = floor((expires_at - now) / 1 day)`, and the status.

## Checks

### Certificate check

- Open a TCP connection to `hostname:443` and do a TLS handshake with SNI set to
  `hostname`. Timeout: 10 s for connect plus handshake.
- Read the leaf certificate's `notAfter` **even if verification fails**, so an
  expired certificate (e.g. `expired.badssl.com`) shows its real, past expiry date
  and negative `days_left`, not just a generic error.
- Also run normal verification (chain + hostname). Store the result as `verified`
  and the reason if it failed (expired, self-signed, hostname mismatch, …).
- Error = no certificate date at all: DNS failure, connection refused, timeout,
  handshake failure.

### Domain check (RDAP)

- Find the registrable domain with the Public Suffix List
  (`expired.badssl.com` → `badssl.com`, `www.example.co.uk` → `example.co.uk`).
- `GET https://rdap.org/domain/<registrable-domain>` and follow redirects to the
  registry's RDAP server. Timeout: 15 s.
- Expiry = `eventDate` of the event with `eventAction: "expiration"`.
- Result is **unknown** (not an error) when: rdap.org has no server for the TLD,
  the response is 404, or the response has no expiration event.
- Result is **error** for network failures, timeouts, HTTP 5xx and invalid JSON.
- Since domain expiry changes rarely, a failed or unknown result does not
  overwrite a known `expires_at` that is less than 7 days old; the error is still
  shown. (Avoids flapping when RDAP is briefly down.)
- Rate limiting: at most 1 RDAP request per second; one request per registrable
  domain per run, even if several monitored hostnames share it. On HTTP 429,
  honour `Retry-After` and mark the result as error if it would exceed the timeout.

### Schedule

- Full run of all domains every 6 hours (`CHECK_INTERVAL_HOURS`, default 6),
  plus one full run at container start.
- On demand: "Check now" per domain and "Check all" in the UI.
- A newly added domain is checked immediately.
- Only one run at a time; an on-demand request for a domain already being
  checked returns without starting a second check.
- Certificate checks for different domains may run in parallel (max 5 at once).

### Status (traffic light)

Per kind, from `days_left`:

| Colour | Rule |
|--------|------|
| green  | `days_left > 30` |
| yellow | `8 <= days_left <= 30` |
| red    | `days_left <= 7` (including already expired), or check error |
| grey   | domain expiry unknown, or not checked yet |

Overall status of a domain = the worse of the two (red > yellow > green > grey).
"Unknown" domain expiry does **not** make a domain red; the overall status then
comes from the certificate alone. A certificate that has a date but fails
verification for a reason other than expiry (e.g. hostname mismatch) is red.

## Alerts

- After each check, for each kind with a known `expires_at`, find the smallest
  threshold in {30, 14, 7} with `days_left <= threshold`.
- If no alert for that (domain, kind, threshold, expires_at) has been sent, send
  one email and record it in `alerts_sent`.
- Only the most urgent threshold is sent. A domain first seen with 5 days left
  gets one 7-day alert, not three emails. Later, lower thresholds still fire
  as they are crossed.
- Already-expired items count as crossing the 7-day threshold (one alert, no repeats).
- A new `expires_at` (renewal) starts a fresh set of thresholds automatically.
- Check errors do not send email in v1 (see Open questions).
- Each alert is a separate email. Subject:
  `[domain-cert-monitor] <hostname>: certificate expires in <n> days`
  (or `domain registration expires …`, or `… expired <n> days ago`).
  Body: hostname, kind, expiry date (UTC), days left, link to the app (`BASE_URL`).
- If sending fails, do not record the alert, so it is retried on the next run.
  Log the SMTP error.
- If SMTP is not configured, alerts are skipped and the UI shows a notice.

## UI

Server-rendered HTML, no login, works without JavaScript for the basics.

**Main page (`/`)**

- Form: hostname input + "Add". Input is normalised (trim, lower-case, strip
  `https://`, path and port; IDN → punycode). Invalid or duplicate input shows a
  clear error message and adds nothing.
- Table, one row per domain, worst status first, then by soonest expiry:
  - overall status dot
  - hostname
  - certificate: expiry date, days left, status dot, error/verification note
  - domain: expiry date, days left, status dot, or "unknown"
  - last checked (relative time, exact UTC on hover)
  - actions: "Check now", "Remove" (with confirmation)
- "Check all" button and the time of the next scheduled run.
- Status is never shown by colour alone: each dot has a text label
  (OK / Soon / Critical / Error / Unknown).

**Settings notice:** a banner if SMTP is not configured.

**Health endpoint:** `GET /healthz` returns 200 with `{"status":"ok"}` when the
app and database are up (used by the Docker healthcheck).

## Deployment

- One image, one container, defined in `docker-compose.yml`.
- Named volume `data` mounted at `/data` (SQLite database).
- Settings in `.env` (an `.env.example` is committed, `.env` is git-ignored):

| Variable               | Default            | Meaning |
|------------------------|--------------------|---------|
| `PORT`                 | `8080`             | HTTP port inside the container |
| `BASE_URL`             | `http://localhost:8080` | used in email links |
| `CHECK_INTERVAL_HOURS` | `6`                | full check interval |
| `ALERT_THRESHOLDS`     | `30,14,7`          | days |
| `SMTP_HOST`            | —                  | alerts disabled if empty |
| `SMTP_PORT`            | `587`              | |
| `SMTP_USER`            | —                  | |
| `SMTP_PASSWORD`        | —                  | |
| `SMTP_SECURITY`        | `starttls`         | `starttls`, `tls` or `none` |
| `SMTP_FROM`            | —                  | sender address |
| `ALERT_TO`             | —                  | comma-separated recipients |
| `TZ`                   | `UTC`              | display time zone |
| `LOG_LEVEL`            | `info`             | |

- Container runs as a non-root user, restarts `unless-stopped`, has a healthcheck
  on `/healthz`.
- Database schema is created / migrated automatically at start.
- Logs go to stdout.

## Acceptance criteria

1. `cp .env.example .env && docker compose up -d` starts the app; `/healthz`
   returns 200 within 30 s.
2. Adding `example.com` shows a row with a certificate expiry date in the future
   and a green or yellow certificate status.
3. Adding `expired.badssl.com` shows the certificate's real, past expiry date,
   negative days left, red status, and a note that the certificate is expired.
4. The domain column for `expired.badssl.com` is based on `badssl.com`.
   For both test hosts it shows either an expiry date or "unknown", never a crash
   or an empty cell.
5. Adding `https://Example.com/path` stores `example.com`; adding it again is
   rejected as a duplicate. Adding `not a domain` is rejected.
6. A hostname that does not resolve (e.g. `does-not-exist.invalid`) shows red
   with a DNS error; other domains are still checked.
7. Status colours follow the table in *Checks → Status* exactly at the
   boundaries: 31 days → green, 30 → yellow, 8 → yellow, 7 → red, 0 → red
   (verified with unit tests using a fixed clock).
8. "Check now" updates "last checked" for that domain; "Check all" updates all.
9. With a fake clock in tests, an item moving from 31 to 5 days left sends one
   7-day alert; moving 31 → 30 → 14 → 7 sends exactly three alerts; running the
   check again sends nothing; a new expiry date resets the alerts.
10. With SMTP pointed at a test mail catcher (e.g. Mailpit), an alert email
    arrives with the subject format above. A failing SMTP server causes a retry
    on the next run, not a lost alert.
11. Removing a domain removes its row, its check results and its alert records.
12. After `docker compose down && docker compose up -d`, all domains and alert
    records are still there.
13. Scheduled runs happen every `CHECK_INTERVAL_HOURS` (verified by log lines
    with a short interval in a test setup).

## Open questions

1. **Tech stack.** Not specified. Suggestion: Python (FastAPI or Flask, stdlib
   `ssl`, `httpx`, APScheduler, `publicsuffix2`/`tldextract` with a bundled list,
   SQLite) — small, one process, easy to containerise. Go would also fit well.
2. **Authentication.** Spec assumes none (trusted network). Is a single shared
   password or HTTP basic auth needed?
3. **Alerts for errors.** Should a check that keeps failing (e.g. host down for
   24 h) send an email? Currently no.
4. **Alerts for domain expiry.** Spec sends alerts for both certificate and
   domain expiry at the same thresholds. Is that wanted, or certificates only?
5. **Unknown domain expiry and status.** Spec shows grey and ignores it for the
   overall status. Should it count as yellow instead?
6. **Verification failures other than expiry** (self-signed, hostname mismatch,
   untrusted chain) are red in this spec. Is that right, or should only expiry
   count?
7. **IPv4 vs. IPv6 / multiple IPs.** Spec checks whatever the resolver returns
   first. Should all A/AAAA records be checked (e.g. load balancers with mixed
   certificates)?
8. **Ports other than 443** and hostnames with a custom port — wanted later?
9. **Recipients.** One global list (`ALERT_TO`), or per-domain recipients?
10. **Alert summary.** One email per alert, or a digest per check run?
11. **Thresholds per domain** — needed, or is the global `30,14,7` enough?
12. **History.** Keep a log of past check results, or only the latest result?
13. **RDAP fallback.** Some ccTLDs (e.g. `.de`) have no public RDAP expiry date.
    Is "unknown" acceptable long-term, or should WHOIS be added later?
