# Changelog

All notable changes to DMARCula. Versions follow [semantic versioning](https://semver.org/).
The running version is shown in the page footer, by `GET /healthz` and by `python -m app.manage version`.

## 1.1.1 – 2026-10-14

- The UI language follows the browser: Slovak for a Slovak browser, English otherwise
  (`DEFAULT_LANGUAGE` now only applies when the browser sends no language; default `en`)
- Test suite: no background DNS threads (fixes an intermittent SQLite lock in tests)

## 1.1.0 – 2026-10-14

- **LDAP and SSO configurable in the web UI** (*Administrácia → Prihlásenie (LDAP / SSO)*), with live
  connection tests and lock-out protection; `python -m app.manage auth-reset` for emergencies
- **English user interface** (SK / EN switch), including charts, DNS findings and logs
- Manual domain management (bulk add, immediate DNS check)
- Installer creates the system user `dmarcula` and installs to `/opt/dmarcula` on every distribution
- Version shown in the footer, on the sign-in page, in `/healthz` and in the installer output
- DNS checks behind split-horizon DNS: resolver failures are reported as *unknown*, and missing
  records contradicted by received reports are flagged
- Author DuprTECH shown in the UI

## 1.0.0 – 2026-10-13

- DMARC aggregate, forensic and TLS-RPT report processing from Exchange Online (Graph), IMAP and uploads
- Dashboard, domains, senders, reports, DNS posture checks (DMARC, SPF, DKIM, MX, MTA-STS, TLS-RPT, BIMI)
- Local, LDAP / Active Directory and OIDC SSO sign-in
- MariaDB / PostgreSQL / SQLite, performance tuned for 140+ domains
- One-command Linux installer, maintenance CLI, cron jobs and backups
