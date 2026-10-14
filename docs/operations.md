# Operations

Day-to-day running of a DMARCula server: scheduled jobs, backups, data retention, monitoring,
logs and maintenance commands. The paths match the [installation guide](installation.md)
(app in `/opt/dmarcula`, system user `dmarcula`, MariaDB database `dmarcula`).

- [What runs automatically](#what-runs-automatically)
- [Maintenance commands](#maintenance-commands)
- [Cron jobs](#cron-jobs)
- [Backups](#backups)
- [Data retention](#data-retention)
- [Monitoring and alerts](#monitoring-and-alerts)
- [Logs](#logs)
- [Certificates](#certificates)
- [Database maintenance](#database-maintenance)
- [Common tasks](#common-tasks)

## What runs automatically

You **don't need cron to fetch reports**. The application has a built-in scheduler:

| Task | When | Setting |
|---|---|---|
| Fetch reports from all enabled mailboxes | every 15 minutes | `POLL_INTERVAL_MINUTES` |
| DNS check of new domains | right after their first report | – |
| DNS re-check of all domains (8 in parallel) | every 24 hours | `DNS_REFRESH_HOURS` |
| Restart after a crash | immediately | systemd `Restart=always` |
| Let's Encrypt renewal | twice a day (only renews when due) | `certbot.timer` (installed with certbot) |

Cron is for everything around it: backups, deleting old data, health checks.

> **Alternative: cron instead of the built-in scheduler.** Set `SCHEDULER_ENABLED=false` in `.env`
> and run `python -m app.manage poll` and `python -m app.manage dns` from cron (see below).
> This suits hosting panels that restart the app often. **Never run both.** The built-in
> scheduler and a cron poll would process the same mailbox at the same time.

## Maintenance commands

All commands run as the `dmarc` user from the app directory, with the same `.env` as the service:

```bash
cd /opt/dmarcula
sudo -u dmarcula .venv/bin/python -m app.manage <command>
```

| Command | What it does | Exit code |
|---|---|---|
| `poll [--source ID]` | fetches reports from all enabled mailboxes (or one) now | 0 |
| `dns [--missing]` | re-runs DNS checks for all domains (or only never-checked ones) | 0 |
| `prune --days N [--dry-run]` | deletes aggregate, forensic and TLS reports and log lines older than N days (minimum 30) | 0 |
| `status [--max-age MIN]` | prints mailbox status and the newest stored report | **1** if a mailbox failed or hasn't run for MIN minutes (default 60) |
| `set-password USERNAME` | sets a local account's password (prompts, or reads `DMARCULA_PASSWORD`) and re-activates it | 2 on error |
| `create-admin USERNAME` | creates a new local admin account | 2 if it exists |

Examples:

```bash
sudo -u dmarcula .venv/bin/python -m app.manage status
sudo -u dmarcula .venv/bin/python -m app.manage prune --days 730 --dry-run
sudo -u dmarcula .venv/bin/python -m app.manage set-password admin        # forgotten admin password
```

Dashboards are cached for up to 5 minutes. Changes made by these commands (e.g. after `prune`)
show up within that time, or immediately after the next report import.

## Cron jobs

The installer puts [`deploy/cron/dmarcula`](../deploy/cron/dmarcula) into **`/etc/cron.d/dmarcula`**.
On Windows, schedule [`deploy/windows/backup.ps1`](../deploy/windows/backup.ps1) and `python -m app.manage prune --days 730`
with Task Scheduler. The installed jobs:

| When | Job | Mails `MAILTO` |
|---|---|---|
| daily 02:30 | MariaDB dump to `/var/backups/dmarcula/dmarcula-YYYY-MM-DD.sql.gz`, files older than 30 days deleted | on error |
| Sundays 03:15 | `app.manage prune --days 730`: delete reports older than 2 years | on error |
| every 30 min | `app.manage status --max-age 60`: check that every mailbox was processed within the last hour | when a mailbox fails or stalls |

To change times, retention or the alert address, edit `/etc/cron.d/dmarcula` as root. Cron
picks up the change automatically. The file looks like this (`dmarcula` = app user):

```cron
SHELL=/bin/bash
PATH=/usr/local/bin:/usr/bin:/bin
MAILTO=admin@example.com

30 2 * * *   dmarcula  mariadb-dump --defaults-extra-file=/opt/dmarcula/.backup.my.cnf --single-transaction --quick dmarcula | gzip > /var/backups/dmarcula/dmarcula-$(date +\%F).sql.gz && find /var/backups/dmarcula -name 'dmarcula-*.sql.gz' -mtime +30 -delete
15 3 * * 0   dmarcula  cd /opt/dmarcula && .venv/bin/python -m app.manage prune --days 730 > /dev/null
*/30 * * * * dmarcula  cd /opt/dmarcula && .venv/bin/python -m app.manage status --max-age 60 > /tmp/dmarcula-status.txt || cat /tmp/dmarcula-status.txt
```

Notes:
- Lines in `/etc/cron.d/` have a **user column** (`dmarcula`) after the schedule, unlike `crontab -e`.
- `%` must be escaped as `\%`.
- `.backup.my.cnf` (created by the installer, mode 600) holds the database credentials for the dump.
- `MAILTO` needs a working local MTA, e.g. `apt install postfix` configured as *Satellite system*
  with your mail server as relay. Without one, cron output goes to `/var/mail/dmarcula`.
- Check that jobs ran: `grep dmarcula /var/log/syslog` (Debian), `journalctl -u cron` or
  `journalctl -u crond`, or `/var/log/cron` (EL).
- Optional extra check that the web app answers (every 5 min):

  ```cron
  */5 * * * * dmarcula  curl -fsS -m 10 -o /dev/null http://127.0.0.1:8000/healthz || echo "DMARCula is not responding"
  ```

### Polling from cron (only with `SCHEDULER_ENABLED=false`)

```cron
*/15 * * * * dmarcula  cd /opt/dmarcula && flock -n /tmp/dmarcula-poll.lock .venv/bin/python -m app.manage poll > /dev/null
40 4 * * *   dmarcula  cd /opt/dmarcula && flock -n /tmp/dmarcula-dns.lock  .venv/bin/python -m app.manage dns  > /dev/null
```

`flock -n` skips a run if the previous one is still going.

### systemd timers instead of cron

If you prefer systemd timers, here is the backup as a timer (the other jobs follow the same pattern):

```bash
sudo tee /etc/systemd/system/dmarcula-backup.service >/dev/null <<'EOF'
[Unit]
Description=DMARCula database backup

[Service]
Type=oneshot
User=dmarcula
ExecStart=/bin/bash -c 'mariadb-dump --defaults-extra-file=/opt/dmarcula/.backup.my.cnf --single-transaction --quick dmarcula | gzip > /var/backups/dmarcula/dmarcula-$(date +%%F).sql.gz && find /var/backups/dmarcula -name "dmarcula-*.sql.gz" -mtime +30 -delete'
EOF

sudo tee /etc/systemd/system/dmarcula-backup.timer >/dev/null <<'EOF'
[Unit]
Description=Daily DMARCula backup

[Timer]
OnCalendar=*-*-* 02:30
Persistent=true

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now dmarcula-backup.timer
systemctl list-timers dmarcula-backup.timer
```

## Backups

What to back up:

| Item | Why |
|---|---|
| MariaDB database `dmarcula` | all reports, domains, users, mailbox settings |
| `/opt/dmarcula/.env` | `SECRET_KEY` decrypts the stored mailbox credentials. Without it they must be re-entered. |
| Apache/nginx vhost, systemd unit | configuration |

The nightly dump from the cron job lands in `/var/backups/dmarcula`. Copy it off the server as
well (your backup system, `rsync`, `restic`, …).

**Restore:**

```bash
sudo systemctl stop dmarcula
gunzip -c /var/backups/dmarcula/dmarcula-2026-10-13.sql.gz | sudo mariadb dmarcula
sudo systemctl start dmarcula
```

**Move to a new server:** run the installer on the new server, stop the service, restore the dump,
copy the old `SECRET_KEY` into the new `.env` (needed to decrypt the mailbox credentials), and start the service.

## Data retention

Reports accumulate indefinitely by default. Measured on test data, the database needs about
**1 KB per report record**, including the original XML kept for the report detail view. For
140 domains (2–7 million records a year), that's roughly **2–8 GB per year**.

- Keep 1–2 years. That's enough for trends and year-over-year comparison:
  `prune --days 730` weekly (see the cron job above).
- Forensic reports may contain personal data (subjects, addresses). If your data protection
  policy requires a shorter period, prune more often with a lower `--days`.
- Domains and their DNS results are not deleted by `prune`. A domain without reports simply
  shows zero traffic. To remove a domain completely, use its detail page (admin).

After a large first prune, give the space back to the file system:

```bash
sudo mariadb -e "OPTIMIZE TABLE dmarcula.aggregate_records, dmarcula.aggregate_reports;"
```

## Monitoring and alerts

| Check | How |
|---|---|
| Application alive | `GET /healthz` returns `{"status":"ok"}` (cron job above, or your monitoring: Zabbix, Uptime Kuma, Nagios, …) |
| Mailboxes processed | `python -m app.manage status --max-age 60`, exit code 1 means a problem (cron job above) |
| Reports still arriving | the `status` output shows the newest stored report; in the UI see *Administrácia → Poštové schránky → Log* |
| Disk space | `df -h /var/lib/mysql /home` |
| Certificate expiry | `sudo certbot certificates` |

Uptime Kuma / Zabbix HTTP check: URL `https://dmarc.example.com/healthz`, expected keyword `ok`.

## Logs

| Log | Where |
|---|---|
| Application (startup, mailbox runs, DNS checks, errors) | `sudo journalctl -u dmarcula -f` (follow), `journalctl -u dmarcula --since today` |
| Per-mailbox processing log | UI: *Administrácia → Poštové schránky → Log spracovania* |
| Apache | `/var/log/apache2/dmarcula-*.log` (EL: `/var/log/httpd/`) |
| nginx | `/var/log/nginx/access.log`, `error.log` |
| MariaDB | `sudo journalctl -u mariadb` |

Apache, nginx and MariaDB logs are rotated by `logrotate` (installed with the packages). If you
use the custom Apache log names from the installation guide, they match the default
`/var/log/apache2/*.log` rule automatically.

The application logs to journald. To cap its disk usage, set a system-wide limit:

```bash
sudo mkdir -p /etc/systemd/journald.conf.d
sudo tee /etc/systemd/journald.conf.d/size.conf >/dev/null <<'EOF'
[Journal]
SystemMaxUse=500M
MaxRetentionSec=3month
EOF
sudo systemctl restart systemd-journald
```

To get a plain log file instead, add these lines to the `[Service]` section of `dmarcula.service`:

```ini
StandardOutput=append:/var/log/dmarcula/dmarcula.log
StandardError=append:/var/log/dmarcula/dmarcula.log
```

```bash
sudo install -d -o dmarc -g dmarc /var/log/dmarcula
sudo tee /etc/logrotate.d/dmarcula >/dev/null <<'EOF'
/var/log/dmarcula/*.log {
    weekly
    rotate 12
    compress
    missingok
    notifempty
    copytruncate
}
EOF
sudo systemctl daemon-reload && sudo systemctl restart dmarcula
```

The ingest log table in the database (shown in the UI) is cleaned by `prune` together with the reports.

## Certificates

- **Let's Encrypt:** renewed automatically by `certbot.timer`. Check with
  `systemctl list-timers certbot.timer` and test with `sudo certbot renew --dry-run`. Certbot
  reloads Apache/nginx after renewal.
- **Own certificate:** replace the files referenced by `SSLCertificateFile` / `ssl_certificate`
  and reload the web server. Add a reminder or a monitoring check for the expiry date:

  ```bash
  openssl x509 -enddate -noout -in /etc/ssl/dmarcula/fullchain.pem
  ```

- **Mailbox app credentials:** an Entra ID client secret expires (max. 24 months). Note the date.
  When it expires, the mailbox log shows `invalid_client` and `status` starts failing. Create a
  new secret and paste it into the mailbox settings. Certificates can have a longer lifetime.

## Database maintenance

MariaDB needs little care. Recommended settings for 100+ domains, in
`/etc/mysql/mariadb.conf.d/90-dmarcula.cnf` (EL: `/etc/my.cnf.d/90-dmarcula.cnf`):

```ini
[mysqld]
innodb_buffer_pool_size = 512M      # ~ size of the dmarcula database, up to 50–70 % of RAM on a dedicated server
max_connections = 50
```

Check the database size:

```bash
sudo mariadb -e "SELECT table_name, ROUND((data_length+index_length)/1024/1024) AS MB
                 FROM information_schema.tables WHERE table_schema='dmarcula' ORDER BY MB DESC;"
```

Updates of DMARCula apply schema changes automatically at startup (update = run the installer again, see [Installation](installation.md)).
Take a backup before every update.

## Common tasks

| Task | How |
|---|---|
| Forgotten admin password | `sudo -u dmarcula .venv/bin/python -m app.manage set-password admin` |
| Locked out (LDAP/SSO broken) | `create-admin rescue`, log in locally (requires `LOCAL_LOGIN_ENABLED=true`), fix the settings, then delete the account |
| Fetch reports right now | UI: *Poštové schránky → Spustiť*, or `python -m app.manage poll` |
| Re-check all DNS now | UI: *Domény → Skontrolovať DNS*, or `python -m app.manage dns` |
| Import historical reports | UI: *Import reportov* (zip/gz/xml/eml) |
| Change `SECRET_KEY` | edit `.env`, restart, then re-enter the mailbox secrets (they can't be decrypted with the new key) |
| Update the application | run the installer again: `sudo bash /opt/dmarcula/deploy/install.sh` |
| Restart | `sudo systemctl restart dmarcula` |
