# domain-cert-monitor — Architecture

Status: draft · Date: 2026-11-21 · Based on: [SPEC.md](SPEC.md)

This document describes how the app described in `SPEC.md` will be built. Where
the two differ, this document wins and the difference is listed in §1.

## 1. Decisions and deviations from SPEC.md

### Decided open questions

| # | Question | Decision |
|---|----------|----------|
| 1 | Tech stack | Python 3.12, FastAPI, SQLite, APScheduler, httpx |
| 2 | Authentication | None. The app runs on a trusted network or behind a reverse proxy. |
| 4 | Alerts for domain expiry | Yes. Certificate and domain expiry both alert, at the same thresholds. |
| 9 | Recipients | One global list (`ALERT_TO`) |
| 12 | History | Only the latest result per domain and kind. No history. |
| — | UI | A JSON API plus a static front end that a design tool will deliver later (see §5) |

### Open questions where the spec default stands

| # | Question | Default kept |
|---|----------|--------------|
| 3 | Alerts for errors | No emails for check errors |
| 5 | Unknown domain expiry | Grey. It doesn't affect the overall status. |
| 6 | Non-expiry verification failures | Red |
| 7 | Multiple IPs | Only the first address the resolver returns is checked |
| 8 | Other ports | Port 443 only |
| 10 | Digest vs. single emails | One email per alert |
| 11 | Per-domain thresholds | No. Thresholds are global. |
| 13 | WHOIS fallback | No. "Unknown" is accepted. |

### Deviations from SPEC.md

1. **UI.** The spec's server-rendered HTML and its "works without JavaScript"
   requirement are replaced by a JSON API (§5) and static files served by the
   same app. The rules for what the UI must show still apply to the future front
   end. These include sort order, text labels next to status dots, the SMTP
   banner and the next run time, and the API provides all the data they need.
2. **`checks.last_success_at`.** This column is not in the spec. The RDAP rule
   "don't overwrite a known expiry less than 7 days old" needs to know when the
   date was last obtained, and `checked_at` only records the last attempt.
3. **`ALERT_THRESHOLDS`** only controls which alerts fire. The status colour bands
   (green > 30, yellow 8–30, red ≤ 7) stay fixed as specified.

## 2. Project layout

```
pyproject.toml            # dependencies, pytest config, entry point
Dockerfile
docker-compose.yml
.env.example              # committed; .env is git-ignored
.dockerignore
.gitignore
src/domain_cert_monitor/
  __init__.py             # __version__
  __main__.py             # load settings, configure logging, run uvicorn (1 worker)
  app.py                  # create_app(): lifespan, routers, static mount
  config.py               # Settings (pydantic-settings) from env / .env
  clock.py                # Clock protocol: SystemClock, FakeClock
  db.py                   # connections, migrations
  repository.py           # all SQL for domains, checks, alerts_sent
  hostnames.py            # input normalisation and validation
  status.py               # days_left, status per kind, overall status, sort key
  checks/
    __init__.py           # CheckResult dataclass
    cert.py               # TLS certificate check
    rdap.py               # registrable domain + RDAP lookup
    ratelimit.py          # async 1 request/second limiter
  runner.py               # CheckRunner: full and single runs, concurrency, merge rule
  alerts.py               # threshold selection, send-and-record
  mailer.py               # SMTP sending, subject/body formatting
  scheduler.py            # APScheduler wiring
  api/
    schemas.py            # pydantic request/response models (the UI contract)
    routes.py             # /api/* and /healthz
static/
  index.html              # placeholder until the real UI arrives; links to /api/docs
tests/
  conftest.py             # temp DB, FakeClock, fake mailer, app fixture
  unit/
  integration/
  network/                # @pytest.mark.network, not run by default
```

Runtime dependencies:
- fastapi
- uvicorn
- pydantic-settings
- httpx
- apscheduler (3.x)
- cryptography
- idna
- tldextract

Development dependencies: pytest, pytest-asyncio.

## 3. Data model

SQLite file at `/data/monitor.db`. Timestamps are stored as ISO-8601 UTC text.

### `domains`

| Column       | Type    | Constraints |
|--------------|---------|-------------|
| `id`         | INTEGER | PRIMARY KEY |
| `hostname`   | TEXT    | NOT NULL UNIQUE (normalised: lower-case ASCII, punycode) |
| `created_at` | TEXT    | NOT NULL |

### `checks`: latest result per domain and kind

| Column            | Type    | Constraints / notes |
|-------------------|---------|---------------------|
| `domain_id`       | INTEGER | NOT NULL, FK → `domains.id` ON DELETE CASCADE |
| `kind`            | TEXT    | NOT NULL, CHECK in (`cert`, `domain`) |
| `checked_at`      | TEXT    | NOT NULL, time of the last attempt |
| `expires_at`      | TEXT    | NULL if unknown or error |
| `last_success_at` | TEXT    | when `expires_at` was last obtained successfully *(addition)* |
| `error`           | TEXT    | NULL on success |
| `details`         | TEXT    | JSON |

Primary key: (`domain_id`, `kind`). Writes are upserts.

`details` content by kind:
- `cert`: `subject`, `issuer`, `sans`, `verified`, `verify_error`, `verify_code`,
  `ip` (the address that was connected to).
- `domain`: `registrable_domain`, `rdap_server` (the final URL after redirects),
  `result` (`ok` / `unknown` / `error`).

### `alerts_sent`

| Column       | Type    | Constraints |
|--------------|---------|-------------|
| `domain_id`  | INTEGER | NOT NULL, FK → `domains.id` ON DELETE CASCADE |
| `kind`       | TEXT    | NOT NULL |
| `threshold`  | INTEGER | NOT NULL |
| `expires_at` | TEXT    | NOT NULL |
| `sent_at`    | TEXT    | NOT NULL |

Unique key: (`domain_id`, `kind`, `threshold`, `expires_at`).

### Access and migrations

- Database access uses the stdlib `sqlite3` module with no ORM. All SQL lives in
  `repository.py`.
- Each unit of work opens a short-lived connection. Every connection sets
  `PRAGMA foreign_keys=ON` and `busy_timeout`. The database runs in WAL mode.
- Migrations are an ordered list of SQL scripts in `db.py`. At startup, the
  scripts after `PRAGMA user_version` are applied in one transaction, and the
  version is bumped.
- `days_left` and the status are always derived in `status.py`. They are never
  stored.
- Calling sync code from async code:
  - API handlers that only touch the database are plain `def`, so FastAPI runs
    them in its threadpool.
  - The async runner calls the repository through `asyncio.to_thread`.

## 4. Modules

### `config.py`

`Settings` holds every variable from the spec's deployment table, with the spec
defaults. It is validated at startup:
- `SMTP_SECURITY` must be `starttls`, `tls` or `none`.
- `ALERT_THRESHOLDS` and `ALERT_TO` are parsed into lists. Thresholds are sorted
  ascending and must be positive.
- `TZ` must be a valid IANA zone.

The derived property `smtp_configured` is true when `SMTP_HOST`, `SMTP_FROM` and
`ALERT_TO` are all set.

### `clock.py`

`Clock` has a single method, `now() -> datetime`, which returns an aware UTC
datetime.
- `SystemClock` is used in production.
- `FakeClock` is used in tests. Its time can be set and advanced.

Every component that needs "now" receives a clock: status, runner, alerts and the
cert check result timestamps.

### `hostnames.py`

`normalise(raw: str) -> str` raises `InvalidHostname` on bad input. Steps:
1. Trim the input.
2. Strip the scheme, userinfo, path, query, fragment and port.
3. Remove a trailing dot and lower-case the rest.
4. Encode to IDNA with UTS46 (`idna` package).
5. Validate:
   - Labels are letters, digits and hyphens (LDH), 1–63 characters each, and the
     whole name is at most 253 characters.
   - There are at least two labels.
   - The TLD is not all digits.
   - IP literals are rejected.

Duplicate detection relies on the UNIQUE constraint on `domains.hostname`.

### `status.py` (pure functions)

- `days_left(expires_at, now) = floor((expires_at - now) / 1 day)`. The result can
  be negative.
- `kind_status(check, kind, now)`:

  | Case | Status |
  |------|--------|
  | No check row yet | grey |
  | Error with no date | red |
  | Domain kind with unknown expiry | grey |
  | `days_left > 30` | green |
  | `8 <= days_left <= 30` | yellow |
  | `days_left <= 7` | red |
  | cert date present, but verification failed for a reason other than expiry | red |

- Labels: green → OK, yellow → Soon, red → Critical (red caused by an error →
  Error), grey → Unknown.
- `overall_status` is the worse of the two, ranked red > yellow > green > grey.
- `sort_key`: worst overall status first, then the soonest known expiry of either
  kind, then hostname.

### `checks/cert.py`

`check_cert(hostname) -> CheckResult`. The blocking socket code runs in
`asyncio.to_thread`.

1. Open a TCP connection to `hostname:443` with `socket.create_connection`, using
   the first address the resolver returns.
2. Do a TLS handshake with `ssl.create_default_context()` and SNI set to the
   hostname. The connect and the handshake share a 10 s deadline.
3. If the handshake succeeds, set `verified=True` and read the certificate with
   `getpeercert(binary_form=True)`.
4. If it raises `ssl.SSLCertVerificationError`, set `verified=False` and record
   `verify_code` and `verify_message`. Then repeat the connect and handshake with
   `check_hostname=False` and `verify_mode=CERT_NONE` to read the leaf certificate
   anyway. Code 10 (`X509_V_ERR_CERT_HAS_EXPIRED`) is shown as "certificate
   expired".
5. Parse the DER bytes with `cryptography.x509` to get `not_valid_after_utc`,
   subject, issuer and DNS SANs.
6. Map failures that leave no certificate date to a short error message:
   - DNS failure (`socket.gaierror`)
   - Connection refused
   - Timeout
   - Other `ssl.SSLError` or `OSError`

   In these cases `expires_at` is NULL.

### `checks/rdap.py`

- `registrable_domain(hostname)` uses `tldextract.TLDExtract(suffix_list_urls=(),
  cache_dir=None)`. That reads the PSL snapshot bundled with the package and never
  touches the network. Examples: `expired.badssl.com` → `badssl.com`,
  `www.example.co.uk` → `example.co.uk`.
- `lookup(registrable) -> CheckResult`:
  - Sends `GET https://rdap.org/domain/<registrable>` through a shared
    `httpx.AsyncClient(follow_redirects=True)`.
  - The whole lookup, including redirects and any 429 wait, has a 15 s deadline.
  - Each lookup first waits for the shared `RateLimiter` (§`ratelimit.py`).
- The expiry is the `eventDate` of the event whose `eventAction` is
  `"expiration"`, parsed to UTC.
- Mapping:

  | Outcome | Result |
  |---------|--------|
  | 200 with an expiration event | ok, `expires_at` set |
  | 404, no RDAP server for the TLD, or 200 without an expiration event | **unknown** (`error` is NULL, `result="unknown"`) |
  | Network error, timeout, 5xx, invalid JSON | **error** |
  | 429 | Wait for `Retry-After` and retry once if it fits in the deadline; otherwise **error** |

### `checks/ratelimit.py`

`RateLimiter(min_interval=1.0)` combines an `asyncio.Lock` with the time of the
last request. Callers `await limiter.wait()` before each request. One instance is
shared by the whole app.

### `runner.py`: `CheckRunner`

The runner holds the shared state:
- `run_lock`, an `asyncio.Lock` held during a full run.
- `in_flight`, a `set[int]` of the domain ids currently being checked.
- `cert_sem`, an `asyncio.Semaphore(5)`.
- The `RateLimiter` and the httpx client.

`run_all() -> bool`
1. If `run_lock` is already held, return False ("already running").
2. Otherwise take the lock and load all domains. Domains already in `in_flight`
   (from a single check) are left out. Add the rest to `in_flight`.
3. Run the cert checks in parallel, at most 5 at once.
4. Group the domains by registrable domain. Do one RDAP lookup per group, one
   after another through the limiter, and apply the result to every hostname in
   the group.
5. For each domain, write both results (merge rule below), evaluate alerts and
   remove the domain from `in_flight`.
6. Catch exceptions per domain and per kind, then log them and store them as
   errors. One failure never aborts the run.
7. Log the start and the end of the run with counts of domains, errors and alerts
   sent.

`run_one(domain_id) -> bool`
- If the domain is in `in_flight`, return False.
- Otherwise check that one domain the same way, using the same semaphore and
  limiter. It doesn't take `run_lock`, so "Check now" works during a full run
  for domains the run doesn't cover. Domains added during a run are not in it.
- The API starts it as a background task (`asyncio.create_task`, keeping a
  reference) and returns at once.

Merge rule for the domain kind: if the new result is unknown or an error, the
existing `expires_at` is not NULL, and `last_success_at` is less than 7 days ago,
then:
- Keep the existing `expires_at` and `last_success_at`.
- Update `checked_at` and `error` (and `details.result`).

In every other case, the new result replaces the stored row, and
`last_success_at` is set when the result is ok.

### `alerts.py`

- `due_threshold(days_left, thresholds) -> int | None` returns the smallest
  threshold `t` with `days_left <= t`, or None. Expired items (negative
  `days_left`) fall under the smallest threshold.
- `process(domain, kind, check, now)` runs after every write. When `expires_at` is
  known and a threshold is due, it looks for an `alerts_sent` row matching
  (domain, kind, threshold, expires_at):
  - If the row exists, do nothing.
  - If SMTP is not configured, log at INFO and skip.
  - Otherwise send the email, then insert the row only if the send succeeded. If
    sending fails, log the SMTP error and insert nothing, so the alert is retried
    on the next run.
- Behaviour that follows from this design:
  - 31 → 5 days sends one 7-day alert.
  - 31 → 30 → 14 → 7 sends three alerts.
  - Running again sends nothing.
  - A new `expires_at` starts fresh, because the unique key includes it.
- Check errors never trigger email.

### `mailer.py`

`Mailer.send(subject, body)` builds an `email.message.EmailMessage` addressed to
every `ALERT_TO` recipient. It runs in `asyncio.to_thread` and uses the connection
type from `SMTP_SECURITY`:

| `SMTP_SECURITY` | Connection |
|-----------------|------------|
| `tls` | `smtplib.SMTP_SSL` |
| `starttls` | `smtplib.SMTP` + `starttls()` |
| `none` | plain `smtplib.SMTP` |

It logs in only when `SMTP_USER` is set. The timeout is 30 s.

Message formatting:
- Subjects:
  - `[domain-cert-monitor] <hostname>: certificate expires in <n> days`
  - `[domain-cert-monitor] <hostname>: domain registration expires in <n> days`
  - `[domain-cert-monitor] <hostname>: certificate expired <n> days ago` (and the
    same form for domain registration)
- Body: hostname, kind, expiry date (UTC), days left and `BASE_URL`.

Tests swap in a fake `Mailer` that records messages and can be told to fail.

### `scheduler.py`

An APScheduler 3 `AsyncIOScheduler` with one interval job:
- `hours=CHECK_INTERVAL_HOURS`
- `next_run_time=now`, so the first full run starts right after startup
- `max_instances=1`
- `coalesce=True`

The job calls `runner.run_all()`. `next_run_at()` reads the job's
`next_run_time` for `/api/meta`.

### `app.py`

`create_app(settings, clock)` builds the app. The lifespan:
1. Runs the migrations.
2. Creates the httpx client, `CheckRunner` and `Mailer`.
3. Starts the scheduler without waiting for the first run, so `/healthz` responds
   immediately.
4. On shutdown, stops the scheduler, cancels running check tasks and closes the
   httpx client.

The API router is mounted first and `StaticFiles` second (see §5).

## 5. JSON API

This is the contract for the future UI. FastAPI serves the OpenAPI schema at
`/api/openapi.json` and Swagger UI at `/api/docs`.

Conventions:
- All timestamps are ISO-8601 UTC (`2026-11-21T14:05:00Z`). The UI converts them
  to `display_timezone`.
- Errors use the shape
  `{"detail": {"code": "<machine_code>", "message": "<human text>"}}`.

| Method & path | Purpose | Responses |
|---|---|---|
| `GET /healthz` | App and DB are up (`SELECT 1`) | 200 `{"status":"ok"}` · 503 if the DB fails |
| `GET /api/meta` | App-level info for banners and headers | 200 |
| `GET /api/domains` | All domains, sorted by `sort_key` | 200 `[Domain]` |
| `POST /api/domains` | Body `{"hostname": "..."}`. Normalise, insert, start an immediate check in the background | 201 `Domain` · 422 `invalid_hostname` · 409 `duplicate` |
| `GET /api/domains/{id}` | One domain | 200 `Domain` · 404 `not_found` |
| `DELETE /api/domains/{id}` | Remove the domain; its checks and alerts are removed by cascade | 204 · 404 |
| `POST /api/domains/{id}/check` | "Check now" | 202 `{"started": bool}` · 404 |
| `POST /api/checks/run` | "Check all" | 202 `{"started": bool}` |

`Meta`:

```json
{
  "version": "0.1.0",
  "smtp_configured": false,
  "run_in_progress": false,
  "next_run_at": "2026-11-21T18:00:00Z",
  "check_interval_hours": 6,
  "alert_thresholds": [7, 14, 30],
  "display_timezone": "UTC"
}
```

`Domain`:

```json
{
  "id": 1,
  "hostname": "expired.badssl.com",
  "created_at": "2026-11-21T14:00:00Z",
  "overall_status": "red",
  "checking": false,
  "last_checked_at": "2026-11-21T14:00:03Z",
  "cert": {
    "status": "red", "label": "Critical",
    "expires_at": "2015-04-12T23:59:59Z", "days_left": -4241,
    "checked_at": "2026-11-21T14:00:03Z", "error": null,
    "verified": false, "verify_error": "certificate expired",
    "details": { "subject": "...", "issuer": "...", "sans": ["*.badssl.com", "badssl.com"], "ip": "..." }
  },
  "domain": {
    "status": "green", "label": "OK",
    "expires_at": "2027-...", "days_left": 400,
    "checked_at": "2026-11-21T14:00:04Z", "error": null,
    "details": { "registrable_domain": "badssl.com", "rdap_server": "https://...", "result": "ok" }
  }
}
```

Rules for the `cert` and `domain` objects:
- Both are always present.
- Before the first check they have `status: "grey"`, `label: "Unknown"` and null
  fields.
- `checking` is true while the domain is in `in_flight`. The UI polls
  `/api/domains` while any row is checking.

**Static UI.** `StaticFiles(directory=STATIC_DIR, html=True)` is mounted at `/`
after the API routes:
- `STATIC_DIR` defaults to `/app/static`.
- Until the design tool delivers a UI, `static/index.html` is a minimal
  placeholder that links to `/api/docs`.
- The delivered UI is a set of plain static files with relative API calls. It is
  copied into `static/` at image build time, or bind-mounted over `/app/static`
  during development.
- No CORS is needed because the UI and the API share an origin.

## 6. Configuration

All settings come from the environment. Compose loads them from `.env`.
`.env.example` lists every variable from the spec's table with its default and a
one-line comment. `.env` is in `.gitignore`.

Internal settings that are not listed in `.env.example`:
- `DATABASE_PATH`, default `/data/monitor.db`
- `STATIC_DIR`, default `/app/static`

Tests use these to point the app at temporary paths.

## 7. Container layout

### Dockerfile

```
# stage 1: build
FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml ./
COPY src ./src
RUN pip wheel --no-cache-dir --wheel-dir /wheels .

# stage 2: runtime
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=8080
RUN useradd --system --uid 10001 --home /app app \
 && mkdir -p /data /app/static && chown app:app /data
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels
COPY static /app/static
VOLUME /data
EXPOSE 8080
USER app
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8080\")}/healthz',timeout=4)"
CMD ["python", "-m", "domain_cert_monitor"]
```

- The container runs as a non-root user (uid 10001).
- `/data` belongs to `app` in the image, so a new named volume takes on that
  ownership.
- `python:3.12-slim` has no curl, so the healthcheck uses Python's `urllib`.
- `__main__` starts uvicorn on `0.0.0.0:$PORT` with **one worker**. The scheduler
  and the in-memory locks live in the process, so several workers would duplicate
  runs.

### docker-compose.yml

```yaml
services:
  monitor:
    build: .
    image: domain-cert-monitor:latest
    env_file: .env
    ports:
      - "${PORT:-8080}:${PORT:-8080}"
    volumes:
      - data:/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "import os,urllib.request;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8080\")}/healthz',timeout=4)"]
      interval: 30s
      timeout: 5s
      start_period: 15s
      retries: 3

  mailpit:                 # optional, for testing alerts: docker compose --profile dev up
    image: axllent/mailpit
    profiles: [dev]
    ports:
      - "8025:8025"        # web UI; app uses SMTP_HOST=mailpit SMTP_PORT=1025 SMTP_SECURITY=none

volumes:
  data: {}
```

- Logs go to stdout as plain text lines (time, level, logger, message). The level
  comes from `LOG_LEVEL`. `docker compose logs` shows them.
- The data survives `docker compose down` and `up`, because the named volume is
  only removed with `down -v`.

## 8. Testing

pytest with pytest-asyncio. Tests use a temporary SQLite file, a `FakeClock` and
a fake `Mailer`.

| Area | Test | Acceptance criterion |
|------|------|----------------------|
| `status.py` | Boundaries: 31 → green, 30 → yellow, 8 → yellow, 7 → red, 0 → red, negative → red; error → red; unknown → grey; overall status and sort order | 7 |
| `alerts.py` + runner | 31 → 5 sends one 7-day alert; 31 → 30 → 14 → 7 sends exactly three; repeating sends nothing; a new `expires_at` resets; a failing mailer means no record, and the next run retries | 9, 10 |
| `hostnames.py` | `https://Example.com/path` → `example.com`; IDN → punycode; `not a domain` rejected | 5 |
| `rdap.py` | `httpx.MockTransport`: ok, 404, no expiration event, 5xx, invalid JSON, 429 with `Retry-After`; registrable domain examples | 4 |
| Merge rule | An unknown or error result keeps a fresh `expires_at` and replaces one 7 or more days old | — |
| Runner | Dedupes RDAP by registrable domain; `run_all` doesn't start twice; `run_one` skips a domain already in flight; one failing domain doesn't stop the others | 6, 8 |
| `cert.py` (integration) | Local TLS server with generated certs: valid (with a test CA), expired, self-signed, hostname mismatch; unresolvable host gives a DNS error | 3, 6 |
| API (integration) | `TestClient`: add, duplicate → 409, invalid → 422, delete cascades, check endpoints return 202, `/healthz` | 5, 8, 11 |
| Mailer (integration) | In-process SMTP server: subject format; refused connection is logged | 10 |
| Network (`-m network`) | `example.com`, `expired.badssl.com`, live rdap.org | 2, 3, 4 |
| Container (manual or CI script) | `cp .env.example .env && docker compose up -d` → `/healthz` returns 200 in 30 s; restart persistence; a short `CHECK_INTERVAL_HOURS` (e.g. `0.01`) logs repeated runs; Mailpit receives an alert | 1, 10, 12, 13 |

`CHECK_INTERVAL_HOURS` is parsed as a float so that the short interval for
criterion 13 can be configured.
