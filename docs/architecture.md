# Architecture

## Components

```
                    ┌──────────────── DMARCator (one uvicorn process) ──────────────┐
 browser ──HTTPS────┤ FastAPI routes ── Jinja2 templates + ECharts (CDN)           │
  (via nginx/Apache)│   │  auth: local (scrypt) · LDAP (ldap3) · OIDC (authlib)    │
                    │   ▼                                                          │
                    │ analysis/stats ─ cache ◄── SQLAlchemy ──► MariaDB / PostgreSQL / SQLite
                    │   ▲                                                          │
                    │ APScheduler ─► ingest/service ─► parser ─► enrich ─► store   │
                    └─────────────────┬────────┬────────────────────┬──────────────┘
                                      ▼        ▼                    ▼
                           Microsoft Graph   IMAP         DNS (PTR, TXT, MX), GeoLite2,
                           (Exchange Online)              HTTPS (MTA-STS policy)
```

| Module | File(s) | Responsibility |
|---|---|---|
| Maintenance CLI | `app/manage.py` | `poll`, `dns`, `prune`, `status`, `set-password`, `create-admin` for cron and admins |
| App | `app/main.py` | FastAPI app, session middleware, security headers, error pages, scheduler, admin bootstrap |
| Config | `app/config.py` | `pydantic-settings`, `.env` loading |
| Database | `app/db.py`, `app/models.py` | engine setup per backend, SQLite WAL/SAVEPOINT handling, ORM models, forward migrations |
| Security | `app/security.py` | password hashing, config encryption (Fernet), CSRF, login throttling, `current_user` / `admin_required` |
| Auth | `app/auth/ldap_auth.py`, `app/auth/oidc.py`, `app/routes/auth.py` | LDAP bind and search, OIDC client, creating and updating external accounts |
| Ingest | `app/ingest/graph.py`, `imap.py` | mailbox access, folder resolution and creation, mark read / move, Graph throttling (429) |
| Parser | `app/ingest/parser.py` | MIME, zip/gzip, aggregate XML (incl. DMARC 2.0 namespace), ARF, TLS-RPT JSON |
| Store | `app/ingest/service.py` | dedup, enrichment, classification, ingest log, running sources and DNS checks |
| Analysis | `app/analysis/*` | `senders` (services, categories), `enrich` (PTR/GeoIP with cache), `dns_checks`, `stats` (aggregations, readiness), `cache` |
| Web | `app/routes/pages.py`, `admin.py`, `app/web.py`, `templates/`, `static/` | pages, admin, Jinja filters, CSS tokens, ECharts helpers |
| i18n | `app/i18n.py`, `app/i18n_en.py` | UI language (cookie `lang`, `/lang/<code>`), Slovak source strings mapped to English. Keys with `{placeholders}` also translate stored messages such as DNS findings by pattern matching. `tests/test_i18n.py` fails if a string lacks a translation or an English page still contains Slovak text. |

## Report processing flow

1. **Scheduler** (`run_all_sources`) runs every `POLL_INTERVAL_MINUTES`. Each source has a lock,
   so manual and scheduled runs never overlap.
2. **Mailbox** returns raw MIME (Graph `GET /messages/{id}/$value`, IMAP `BODY.PEEK[]`), at most
   500 messages per run.
3. **Parser** detects the content:
   - `multipart/report; report-type=feedback-report` → forensic report,
   - zip/gz/xml/json attachments → unpacked (200 MB cap) → `<feedback>` = aggregate report,
     JSON with `policies` = TLS-RPT.
4. **Store** (`store`) per report:
   - dedup: aggregate by `(org_name, report_id, domain)`, TLS by `(org, report_id,
     policy_domain)`, forensic by a SHA-256 of the headers,
   - IP enrichment (PTR, country, ASN), cached in `ip_info` for 14 days and resolved 16 at a time,
   - service identification (`identify`) and category (`categorize`),
   - the first DKIM/SPF result is denormalized into columns for SQL grouping,
   - the domain is created or updated.
   Each report is stored in its own SAVEPOINT, so one bad report doesn't discard the others.
5. The message is marked read and/or moved. The result goes to `ingest_log` and `mail_sources.last_*`.
6. **New domains** get an immediate DNS check. All domains are re-checked every
   `DNS_REFRESH_HOURS`, 8 at a time.
7. The aggregate **cache is invalidated** whenever new reports are stored or DNS results change.

## Data model

```
users                 mail_sources (config_encrypted)       ingest_log
domains               ip_info (PTR/GeoIP cache)
aggregate_reports 1───* aggregate_records
forensic_reports      tls_reports
```

| Table | Key columns |
|---|---|
| `aggregate_reports` | org, report_id, domain, date_begin/end, published policy (p, sp, np, pct, adkim, aspf, fo), raw XML (`LONGTEXT` on MariaDB). Indexes: unique `(org_name, report_id, domain)`, `(domain, date_end)`. |
| `aggregate_records` | source_ip, count, disposition, dmarc_pass, spf_aligned, dkim_aligned, dkim/spf results (JSON) plus first result as columns, PTR, sender, **category**, country. Denormalized `domain`, `day`, `org_name` for fast aggregation. Indexes `(domain, day)`, `(day)`, `(source_ip)`, `(category)`. |
| `forensic_reports` | fingerprint, arrival_date, source_ip, reported_domain, auth_failure, subject, feedback fields (JSON), headers |
| `tls_reports` | one row per policy: policy_domain, type, MX hosts, success/failure counts, failure_details (JSON) |
| `domains` | name, last DNS check result (JSON), score, timestamps |

**Migrations:** at startup `init_db()` creates missing tables, adds missing nullable columns
(`ALTER TABLE … ADD COLUMN`), creates missing indexes, and backfills data where a new column
needs it. No external migration tool is required.

## Performance

Measured with a synthetic dataset of 140 domains × 90 days (20 k reports, 107 k records):

| | SQLite | MariaDB 11.4 (default config) |
|---|---|---|
| Dashboard, 30 days, uncached (all aggregate queries) | ~0.5 s | ~1.4 s |
| Dashboard, 30 days, cached | ~10 ms | ~15 ms |
| Domains list (140 rows), cached | ~20 ms | ~30 ms |

Uncached means the first page view after new reports arrive (at most once per poll interval).

What keeps it fast:
- every dashboard and readiness aggregation runs a **fixed number of grouped queries**,
  independent of the domain count,
- "new failing sources" only scans the last 7 days and uses an index-backed `NOT EXISTS`,
- results are cached in-process and invalidated on ingest, so most page views cost no SQL at all,
- for MariaDB, set `innodb_buffer_pool_size` so the records table fits in memory.

## Security

| Area | Implementation |
|---|---|
| Sessions | signed cookie (`itsdangerous`), `SameSite=Lax`, `Secure` with `COOKIE_SECURE`, absolute expiry |
| CSRF | per-session token, verified on every POST (`csrf_token` field or `X-CSRF-Token` header) |
| Passwords | `scrypt` (N=2^14, r=8, p=1), 16-byte salt, constant-time compare |
| Brute force | 8 failures per IP + username per 5 minutes |
| Mailbox credentials | Fernet (AES-128-CBC + HMAC), key = SHA-256 of `SECRET_KEY`. Secrets are never sent back to the form. |
| LDAP | filter escaping, password verified by bind, LDAPS/StartTLS with certificate verification |
| OIDC | authorization code flow with `state` and `nonce` (authlib), group allow-list, cross-source account takeover prevention |
| Input | `defusedxml` (XXE, billion laughs), archive and attachment size limits, JSON in `<script>` with `<`, `>`, `&` escaped |
| Redirects | the post-login `next` target must be a relative path |
| Headers | `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin` |
| Authorization | `viewer` is read-only; everything under `/admin` requires `admin` |

## HTTP interface

| Method and path | Role | Description |
|---|---|---|
| `GET /healthz` | – | health check |
| `GET /login`, `POST /login`, `POST /logout` | – | local and LDAP login |
| `GET /auth/oidc/login`, `/auth/oidc/callback` | – | SSO |
| `GET /`, `/domains`, `/domains/{d}`, `/sources`, `/reports`, `/reports/{id}`, `/forensic`, `/forensic/{id}`, `/tls`, `/tools`, `/profile` | viewer | pages (query params `days`, `domain`, `start`, `end`) |
| `GET /sources.csv`, `/reports/{id}/xml` | viewer | exports |
| `POST /domains/{d}/check` | viewer | re-check DNS of one domain |
| `GET /api/dashboard?days=&domain=` | viewer (session) | JSON with summary, timeline, categories, senders and domains |
| `/admin/sources*`, `/admin/upload`, `/admin/users*`, `/admin/dns-refresh`, `/admin/domains/{id}/delete` | admin | administration |

## Extending

- **New known service:** add its domain to `KNOWN_SENDERS` in `app/analysis/senders.py`. The key is
  a domain or suffix from the PTR, DKIM `d=` or SPF domain. Only reports stored after the change
  are classified with it.
- **New chart:** add a query to `app/analysis/stats.py`, pass the data to the template, and draw it
  with a helper from `app/static/js/app.js` (`DM.volume`, `DM.donut`, `DM.hbar`, …). Take colors from
  the CSS variables (`--series-1..8`, `--good`, `--warning`, `--critical`) so dark mode works.
- **New source type:** implement a class with `test()`, `fetch(limit)` (yields `(id, subject, raw_bytes)`),
  `done(id, ok)` and `close()`, and register it in `open_mailbox()`.

## Development and tests

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest ruff
pytest                                    # parser + full app flow (login, CSRF, upload, every page)
TEST_DATABASE_URL="mysql+pymysql://user:pass@localhost/dmarcator_test" pytest   # same suite on an empty MariaDB DB
ruff check app tests
python -m app.demo --days 60 --domains 140      # synthetic demo data
uvicorn app.main:app --reload
```
