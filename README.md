# DMARCula – DMARC Analyzer

DMARCula is a self-hosted web application that collects DMARC reports from an **Exchange
Online (Microsoft 365)** mailbox, an IMAP mailbox or manual uploads. It parses them, works out which service
sent each message (Microsoft 365, SendGrid, Mailchimp, …) and checks the DNS posture of every
domain. The results are shown on a dashboard built for managing **many domains** at once.
It's inspired by dmarcian, Valimail, EasyDMARC and parsedmarc.

**Author:** [DuprTECH](https://github.com/DuprTECH) · The user interface is available in **Slovak and English** (switch in the top bar). The documentation is in English.

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
| **Domains, manual** | add domains by hand (bulk, one per line). They are checked immediately and then daily, even before the first report arrives |
| **Tools** | DNS check of any domain, DMARC record generator, CSV export, dark mode, responsive layout, **Slovak / English UI** |
| **Databases** | MariaDB / MySQL, PostgreSQL or SQLite. Schema and indexes are created and upgraded automatically. |

## Installation

Pick your operating system. Each guide is a numbered list of commands to copy and paste,
always in the same order:

**1. packages → 2. database (MariaDB) → 3. `git clone` + Python → 4. configuration (`.env`) →
5. service → 6. web server (Apache / IIS) → 7. HTTPS → 8. cron / scheduled tasks → 9. first login**

| Operating system | Step-by-step guide |
|---|---|
| **Debian 12+ / Ubuntu 22.04+** | [docs/deploy-debian.md](docs/deploy-debian.md) |
| **RHEL / Rocky Linux / AlmaLinux 8–9** | [docs/deploy-rhel.md](docs/deploy-rhel.md) |
| **openSUSE Leap / SLES 15** | [docs/deploy-suse.md](docs/deploy-suse.md) |
| **Windows Server 2019+ (IIS)** | [docs/deploy-windows.md](docs/deploy-windows.md) |
| Arch, Fedora, Alpine, other Linux | [docs/installation.md → Other distributions](docs/installation.md#other-distributions) |
| Any OS with Docker | [docs/installation.md → Docker Compose](docs/installation.md#docker-compose) |

**Prefer one command?** On Debian/Ubuntu, RHEL-family and openSUSE, the installer does all of the steps
above after asking for the host name and admin password. Running it again updates the app.

```bash
curl -fsSL https://raw.githubusercontent.com/DuprTECH/DMARCula---DMARC-Anylzer/main/deploy/install.sh -o install.sh
sudo bash install.sh
```

After installation:
- connect the Exchange Online mailbox: [docs/microsoft-365.md](docs/microsoft-365.md),
- optionally enable LDAP or SSO login: [docs/authentication.md](docs/authentication.md),
- backups, cron jobs, logs, updates: [docs/operations.md](docs/operations.md).

> The application runs as a service behind Apache / nginx / IIS. **Never put it into
> `public_html`** or any other web root, because `.env` with the passwords would become downloadable.

## Requirements

- Python 3.10+ (3.11 / 3.12 recommended)
- MariaDB 10.6+, PostgreSQL 14+ or SQLite
- outbound access to Microsoft Graph / your IMAP server and to DNS
- a single application process (the mailbox scheduler runs in-process)

## Development

A local try-out with SQLite and demo data, plus the test suite, is described in
[docs/architecture.md → Development and tests](docs/architecture.md#development-and-tests).
