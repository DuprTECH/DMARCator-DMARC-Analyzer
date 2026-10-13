# DMARCula – DMARC Analyzer

DMARCula is a self-hosted web application that collects DMARC reports from an **Exchange
Online (Microsoft 365)** mailbox, an IMAP mailbox or manual uploads. It parses them, works out which service
sent each message (Microsoft 365, SendGrid, Mailchimp, …) and checks the DNS posture of every
domain. The results are shown on a dashboard built for managing **many domains** at once.
It's inspired by dmarcian, Valimail, EasyDMARC and parsedmarc.

The user interface is in Slovak. The documentation is in English.

📖 **Full documentation: [docs/](docs/README.md)**: [Installation](docs/installation.md) · [Operations](docs/operations.md) ·
[Configuration](docs/configuration.md) · [Authentication](docs/authentication.md) ·
[Microsoft 365 setup](docs/microsoft-365.md) · [User guide](docs/user-guide.md) ·
[Architecture](docs/architecture.md)

## Features

| Area | What it does |
|---|---|
| **Login** | local accounts, **LDAP / Active Directory** (LDAPS/StartTLS, service account or direct UPN bind, group filter, nested groups), **SSO via OpenID Connect** (Entra ID, Keycloak, Okta, ADFS, …). `admin` / `viewer` roles can be mapped from groups. |
| **Ingest** | **Microsoft Graph** (Exchange Online, app-only, client secret or certificate, scoped with RBAC for Applications), **IMAP** (password or OAuth2 XOAUTH2 for Exchange Online), manual upload (.xml, .gz, .zip, .json, .eml). Processed messages are marked read and/or moved, and duplicates are skipped. |
| **Reports** | aggregate (RUA), including the DMARC 2.0 namespace; forensic (RUF / ARF); SMTP TLS reporting (TLS-RPT, RFC 8460) |
| **Classification** | compliant / forwarded / non-compliant legitimate source / threat, dmarcian-style. Recognizes 60+ sending services and M365 tenants without custom DKIM. |
| **Dashboard** | KPIs compared with the previous period, daily volume, compliance trend, SPF/DKIM alignment, applied policy, reporters, countries (map), services, domains with sparklines, new failing sources, recommendations |
| **Domains** | A–F DNS score: DMARC (incl. external `rua` authorization), SPF (recursive lookup count, void lookups, `+all`/`ptr`), DKIM (selectors from reports + common ones, key length), MX, MTA-STS (fetches and validates the policy), TLS-RPT, BIMI. Plus **readiness for `p=quarantine` / `p=reject`**. Search and filters for 100+ domains. |
| **Tools** | DNS check of any domain, DMARC record generator, CSV export, dark mode, responsive layout |
| **Databases** | MariaDB / MySQL, PostgreSQL or SQLite. Schema and indexes are created and upgraded automatically. |

## Quick start

**Production on Linux (Debian, Ubuntu, RHEL/Rocky/Alma, …) with MariaDB:** follow the
step-by-step commands in [docs/installation.md](docs/installation.md).

**Docker Compose (PostgreSQL):**

```bash
cp .env.example .env          # set SECRET_KEY, ADMIN_PASSWORD, BASE_URL, POSTGRES_PASSWORD
docker compose up -d --build
```

**Local try-out (SQLite, demo data):**

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # set SECRET_KEY and ADMIN_PASSWORD
python -m app.demo --days 60 --domains 20
uvicorn app.main:app --port 8000
```

The app listens on `http://127.0.0.1:8000`. In production, put nginx or Apache with HTTPS in
front of it, and **never install it inside `public_html`** or any other web root.

## Requirements

- Python 3.10+ (3.11 / 3.12 recommended)
- MariaDB 10.6+, PostgreSQL 14+ or SQLite
- outbound access to Microsoft Graph / your IMAP server and to DNS
- a single application process (the mailbox scheduler runs in-process)

## Tests

```bash
pip install pytest
pytest                                                    # SQLite
TEST_DATABASE_URL="mysql+pymysql://u:p@localhost/dmarcula_test" pytest   # empty MariaDB database
```
