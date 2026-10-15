# Changelog

All notable changes to DMARCator (formerly DMARCula). Versions follow [semantic versioning](https://semver.org/).
The running version is shown in the page footer, by `GET /healthz` and by `python -m app.manage version`.

## 1.2.0 – 2026-10-15

- **Renamed from DMARCula to DMARCator.** Another DMARC service already uses the DMARCula name.
  New name, logo (an envelope under a magnifying glass) and brand colour in the UI; repository moved to
  `DuprTECH/DMARCator-DMARC-Analyzer` (GitHub redirects the old URL).
- Service, directory, system user, database, cron file and backups are now named `dmarcator`.
  `install.sh` renames an existing DMARCula installation automatically
  ([upgrade notes](docs/installation.md#upgrading-from-dmarcula-before-120)).
- Compatibility: `APP_NAME=DMARCula` in `.env` is shown as DMARCator, an existing
  `data/dmarcula.db` keeps being used, `DMARCULA_PASSWORD` still works for `set-password`, and
  stored mailbox credentials and the processed-message marker in Exchange Online are unchanged.
- Backup cron template takes the database name as a placeholder (`__DB__`).
- Installer updates no longer stop on `Not possible to fast-forward` after upstream history was
  rewritten: a checkout without local edits is reset to the repository (the old commit stays in
  `git reflog`); local edits stop the installer before anything is changed.

## 1.1.7 – 2026-10-15

- Admin **Time and language** page: shared IANA time zone with automatic DST conversion for
  displayed timestamps, and default UI language. Changes take effect without a restart.
- Personal language switches still take priority; **AUTO** clears the override.
- Database timestamps, report-day aggregations, CLI output and server logs remain unchanged (UTC
  for stored timestamps). The selected display zone is shown in the footer and mailbox log header.

## 1.1.6 – 2026-10-15

- Optional age-based retention for global Exchange Online / Graph mailboxes: checkbox and retention
  days, off by default. Permanently deletes up to 500 old, explicitly marked successful report
  messages per poll from a dedicated processed folder. No quota or 10% free-space guarantee.
- **Fix:** messages without reports and reports that failed to store now use the configured failed
  folder, never the successful-report retention path.

## 1.1.5 – 2026-10-14

- Forensic reports in non-standard formats are recognised: `message/feedback-report` anywhere in the
  message, ARF fields as plain text, or only "… Forensic Report for <domain> from IP <ip>" in the subject
- **Fix:** `Data too long for column 'ip'` – the source IP is taken as the first valid address of the
  field, and all text values are trimmed to the column size (MariaDB strict mode rejected them)

## 1.1.4 – 2026-10-14

- **Fix:** a database error while storing one report (e.g. MariaDB *server has gone away*) left the
  session broken (`PendingRollbackError`), and the same message blocked the mailbox on every run.
  Each report is now stored in its own transaction, a failing message is logged with its real cause
  and skipped, and processing continues with the next messages.
- Report XML larger than `RAW_XML_MAX_KB` (default 2 MB) is no longer stored verbatim, so it stays
  under MariaDB's `max_allowed_packet`
- Aggregate reports without `date_range` (seen from some senders) are accepted; the period is taken
  from the Unix timestamp in the file name or the day of receipt

## 1.1.3 – 2026-10-14

- Charts for periods longer than 120 days (e.g. 1 year) are aggregated per week – daily bars were
  thinner than a pixel and appeared empty
- Chart axis numbers use the UI language's number format

## 1.1.2 – 2026-10-14

- License: PolyForm Internal Use 1.0.0 – free internal use in any organization; no resale,
  redistribution, hosted services for third parties or rebranding (see README → License)

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
