# DMARCula – DMARC Analyzer

DMARCula is a self-hosted web application that collects DMARC reports from an **Exchange
Online (Microsoft 365)** mailbox, an IMAP mailbox or manual uploads. It parses them, works out which service
sent each message (Microsoft 365, SendGrid, Mailchimp, …) and checks the DNS posture of every
domain. The results are shown on a dashboard built for managing **many domains** at once.
It's inspired by dmarcian, Valimail, EasyDMARC and parsedmarc.

**Author:** [DuprTECH](https://github.com/DuprTECH) · **License:** [PolyForm Internal Use 1.0.0](LICENSE) (free for internal use in any company) · **Version:** 1.1.7 ([changelog](CHANGELOG.md)) · The user interface is available in **Slovak and English** (switch in the top bar). The documentation is in English.

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
| **Dashboard** | KPIs compared with the previous period, volume and compliance trends, SPF/DKIM alignment, applied policy, reporters, country breakdown, services, domains with sparklines, new failing sources, recommendations |
| **Domains** | A–F DNS score: DMARC (incl. external `rua` authorization), SPF (recursive lookup count, void lookups, `+all`/`ptr`), DKIM (selectors from reports + common ones, key length), MX, MTA-STS (fetches and validates the policy), TLS-RPT, BIMI. Plus **readiness for `p=quarantine` / `p=reject`**. Search and filters for 100+ domains. |
| **Domains, manual** | add domains by hand (bulk, one per line). They are checked immediately and then daily, even before the first report arrives |
| **Tools** | DNS check of any domain, DMARC record generator, CSV export, dark mode, responsive layout, **Slovak / English UI** |
| **Databases** | MariaDB / MySQL, PostgreSQL or SQLite. Schema and indexes are created and upgraded automatically. |
| **Sender investigation** | Search source IPs, PTR names, services and domains; inspect common DKIM/SPF failure combinations; filter traffic categories and export the sender table to CSV. |
| **Report details** | Inspect aggregate records, authentication results and policy override reasons; download retained original XML. Forensic views show message headers and failure fields; TLS-RPT lists TLS delivery successes and failures. |
| **IP enrichment** | Reverse DNS, plus optional country and ASN lookups using local MaxMind GeoLite2 databases. Countries remain unknown without a configured, readable country database. No third-party IP lookup API is required. |
| **Reliable processing** | Scheduled polling, per-report database transactions, duplicate detection, per-message error isolation and source status/logs. Reports without a date range have a fallback period; common non-standard forensic formats are supported. |
| **Mailbox retention** | Opt-in checkbox and retention days for global Microsoft Graph. Permanently deletes old, explicitly marked successful reports from a dedicated processed folder, at most 500 per run. Disabled by default; not quota monitoring and **does not guarantee 10% free space**. |
| **Time and language** | Web-admin settings for a shared IANA time zone (including automatic daylight-saving changes) and default language. Personal SK/EN overrides and AUTO reset. Stored timestamps remain UTC. |
| **Operations** | Built-in polling and DNS scheduler, health/version endpoint, CLI mailbox status, manual polling/DNS refresh, database report/log pruning, password/admin recovery, and documented backup/cron workflows. |

### What the portal helps you do

1. **Centralize reporting** across many domains and mailboxes, from ingestion to searchable report details.
2. **Find legitimate senders that fail DMARC** and distinguish them from forwarding and unknown sources.
3. **Plan a stricter DMARC policy** using observed legitimate-mail compliance and domain readiness recommendations.
4. **Audit email DNS configuration** and generate a DMARC TXT record to publish with your DNS provider.
5. **Track changes over time**, including new failing IPs, service volumes and domain compliance.
6. **Operate internally** with administrator/viewer roles, local/LDAP/OIDC sign-in, multilingual UI and maintenance tools.

DMARCula analyzes reports and makes recommendations: it is **not an email gateway**, does not
block mail itself, and does not automatically publish DNS changes. Service/threat classification
and policy readiness are diagnostic guidance, not proof that a message is malicious or that a
policy change is risk-free. GeoIP describes the sending IP's estimated location, not the sender's
identity. See the [user guide](docs/user-guide.md) for interpretation and the
[configuration guide](docs/configuration.md) for optional GeoIP and mailbox-retention prerequisites.

## Screenshots

These previews have identifying fields replaced with reserved example domains, documentation
IP addresses and sample text. Some aggregate counts and chart shapes are retained to illustrate
the layout; the images are **not a consistent demo dataset**. Long tables are cropped and
account identities are hidden. Only flattened, metadata-free anonymized copies are included.

### Overview

Compliance, volume, alignment, services, recommendations and domain trends in one dashboard.

![Anonymized DMARCula overview dashboard](docs/screenshots/overview.png)

<details>
<summary><strong>Domains — policy, DNS health and compliance</strong></summary>

Search and filter domains, compare DNS scores, review policies and spot domains needing attention.

![Anonymized domain policy and compliance table](docs/screenshots/domains.png)

</details>

<details>
<summary><strong>Senders — services, failure combinations and source IPs</strong></summary>

Investigate SPF/DKIM combinations and source categories. This formerly long screenshot is
cropped to the summary and the first source rows.

![Anonymized sender analysis with shortened source table](docs/screenshots/senders.png)

</details>

<details>
<summary><strong>Aggregate reports (RUA)</strong></summary>

Browse reporting organizations, report periods, policies and message compliance.

![Anonymized aggregate report list](docs/screenshots/aggregate.png)

</details>

<details>
<summary><strong>Forensic reports (RUF)</strong></summary>

Inspect individual failure metadata. Actual subjects, addresses, domains, hostnames and IPs
have been replaced in this preview.

![Anonymized forensic report metadata](docs/screenshots/forensic.png)

</details>

<details>
<summary><strong>Tools — DNS audit and DMARC record generator</strong></summary>

Review DMARC, SPF, DKIM, MX, MTA-STS, TLS-RPT and BIMI checks, and prepare a DMARC TXT record.
DNS values in this screenshot are illustrative replacements, not deployable configuration.

![Anonymized DNS audit and DMARC record generator](docs/screenshots/tools.png)

</details>

<details>
<summary><strong>Mailbox administration and processing log</strong></summary>

Configure mailbox sources and inspect polling results and processing status.

![Anonymized mailbox administration and shortened processing log](docs/screenshots/mailboxes.png)

</details>

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
