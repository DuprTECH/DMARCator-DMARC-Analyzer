# DMARCula – DMARC Analyzer

DMARCula is a self-hosted web application that collects DMARC reports from an **Exchange
Online (Microsoft 365)** mailbox, an IMAP mailbox or manual uploads. It parses them, works out which service
sent each message (Microsoft 365, SendGrid, Mailchimp, …) and checks the DNS posture of every
domain. The results are shown on a dashboard built for managing **many domains** at once.
It's inspired by dmarcian, Valimail, EasyDMARC and parsedmarc.

**Author:** [DuprTECH](https://github.com/DuprTECH) · **License:** [PolyForm Internal Use 1.0.0](LICENSE) (free for internal use in any company) · **Version:** 1.1.2 ([changelog](CHANGELOG.md)) · The user interface is available in **Slovak and English** (switch in the top bar). The documentation is in English.

📖 **Full documentation: [docs/](docs/README.md)**: [Installation](docs/installation.md) · [Operations](docs/operations.md) ·
[Configuration](docs/configuration.md) · [Authentication](docs/authentication.md) ·
[Microsoft 365 setup](docs/microsoft-365.md) · [User guide](docs/user-guide.md) ·
[Architecture](docs/architecture.md)

## Features

| Area | What it does |
|---|---|
| **Login** | configured in the web UI with built-in connection tests: local accounts, **LDAP / Active Directory** (LDAPS/StartTLS, service account or direct UPN bind, group filter, nested groups), **SSO via OpenID Connect** (Entra ID, Keycloak, Okta, ADFS, …). `admin` / `viewer` roles can be mapped from groups. |
| **Ingest** | **Microsoft Graph** (Exchange Online, app-only, client secret or certificate, scoped with RBAC for Applications), **IMAP** (password or OAuth2 XOAUTH2 for Exchange Online), manual upload (.xml, .gz, .zip, .json, .eml). Processed messages are marked read and/or moved, and duplicates are skipped. |
| **Reports** | aggregate (RUA), including the DMARC 2.0 namespace; forensic (RUF / ARF); SMTP TLS reporting (TLS-RPT, RFC 8460) |
| **Classification** | compliant / forwarded / non-compliant legitimate source / threat, dmarcian-style. Recognizes 60+ sending services and M365 tenants without custom DKIM. |
| **Dashboard** | KPIs compared with the previous period, daily volume, compliance trend, SPF/DKIM alignment, applied policy, reporters, countries (map), services, domains with sparklines, new failing sources, recommendations |
| **Domains** | A–F DNS score: DMARC (incl. external `rua` authorization), SPF (recursive lookup count, void lookups, `+all`/`ptr`), DKIM (selectors from reports + common ones, key length), MX, MTA-STS (fetches and validates the policy), TLS-RPT, BIMI. Plus **readiness for `p=quarantine` / `p=reject`**. Search and filters for 100+ domains. |
| **Domains, manual** | add domains by hand (bulk, one per line). They are checked immediately and then daily, even before the first report arrives |
| **Tools** | DNS check of any domain, DMARC record generator, CSV export, dark mode, responsive layout, **Slovak / English UI** |
| **Databases** | MariaDB / MySQL, PostgreSQL or SQLite. Schema and indexes are created and upgraded automatically. |

## Installation

On a Linux server (Debian, Ubuntu, RHEL / Rocky / Alma, Fedora, openSUSE):

```bash
curl -fsSL https://raw.githubusercontent.com/DuprTECH/DMARCula---DMARC-Anylzer/main/deploy/install.sh -o install.sh
sudo bash install.sh
```

The installer asks for the host name and admin password. It then creates the system user `dmarcula`
and installs MariaDB, the application (`/opt/dmarcula`), the systemd service, Apache or nginx with HTTPS, and cron jobs for backups.
Running it again updates the application.

Then add the report mailbox in *Administrácia → Poštové schránky*
([Microsoft 365 setup](docs/microsoft-365.md)).

Existing sites, company certificates, Docker, Windows and troubleshooting are covered in
[docs/installation.md](docs/installation.md).

## Requirements

- Python 3.10+ (3.11 / 3.12 recommended)
- MariaDB 10.6+, PostgreSQL 14+ or SQLite
- outbound access to Microsoft Graph / your IMAP server and to DNS
- a single application process (the mailbox scheduler runs in-process)

## Development

A local try-out with SQLite and demo data, plus the test suite, is described in
[docs/architecture.md → Development and tests](docs/architecture.md#development-and-tests).

## License

DMARCula is **source-available** under the [PolyForm Internal Use License 1.0.0](LICENSE).
Copyright © 2026 DuprTECH.

In plain words (the [LICENSE](LICENSE) text is binding):

- ✅ **Any company or organization may use DMARCula for its own internal operations, for free.**
  This includes commercial companies. You may install it, run it and adapt it for your own needs.
- ❌ You may **not** sell, rent, sublicense or redistribute the software, or offer it to third
  parties as a hosted or paid service.
- ❌ You may **not** publish or distribute it, or a modified version, under another name.
  "DMARCula" and "DuprTECH" are names of the author and may not be used for other products or services.
  The author attribution shown in the application must not be removed.

For any other use (redistribution, resale, hosting for customers, partnership), contact
[DuprTECH](https://github.com/DuprTECH).

