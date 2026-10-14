# Installation

## Install (Linux)

Works on **Debian 11+, Ubuntu 20.04+, RHEL / Rocky / AlmaLinux / CentOS Stream 8+, Fedora,
openSUSE / SLES 15**. You need root, and a DNS name such as `dmarc.example.com` pointing to the server.

```bash
curl -fsSL https://raw.githubusercontent.com/DuprTECH/DMARCula---DMARC-Anylzer/main/deploy/install.sh -o install.sh
sudo bash install.sh
```

The installer asks for:
- the host name,
- Apache or nginx,
- whether to get a Let's Encrypt certificate,
- the admin password (or generates one),
- an optional e-mail address for alerts.

Then it does everything else:

| | |
|---|---|
| packages | Python, Git, MariaDB, Apache or nginx, certbot, cron |
| database | MariaDB database `dmarcula` and a user with a random password |
| application | system user `dmarcula` (created if missing), `git clone` into `/opt/dmarcula`, Python venv, `.env` with a random `SECRET_KEY` |
| service | systemd unit `dmarcula` on `127.0.0.1:8000` |
| web server | virtual host for your name, firewall ports, SELinux boolean, HTTPS |
| cron | daily DB backup to `/var/backups/dmarcula`, weekly cleanup of reports older than 2 years, mailbox alerts |

At the end it prints the URL and the admin password. Then:
1. sign in and add the report mailbox: *Administrácia → Poštové schránky* ([Microsoft 365 setup](microsoft-365.md)),
2. optionally enable LDAP or SSO in *Administrácia → Prihlásenie (LDAP / SSO)* ([Authentication](authentication.md)).

**Update:** run the installer again: `sudo bash /opt/dmarcula/deploy/install.sh`. It detects the installation, pulls the new version, restarts the service and prints the version.

The application runs as a service; the web server only forwards requests to it. Nothing is placed in a web root.

## Special cases

**Existing site / hosting panel.** If a virtual host for the name already exists, or a panel such as
Virtualmin, HestiaCP or ISPConfig is detected, the installer leaves the web server alone and prints
these lines. Add them to the existing `<VirtualHost *:443>` (in a panel: the site's custom Apache directives):

```apache
ProxyPreserveHost On
ProxyPass        / http://127.0.0.1:8000/ timeout=120
ProxyPassReverse / http://127.0.0.1:8000/
RequestHeader set X-Forwarded-Proto "https" env=HTTPS
```

**Internal name / company certificate.** Answer `no` to Let's Encrypt and replace
`/etc/apache2/sites-available/dmarcula.conf` with the following. On RHEL the file is
`/etc/httpd/conf.d/dmarcula.conf`. Comments must stay on their own lines; Apache rejects comments after a directive.

```apache
<VirtualHost *:80>
    ServerName dmarc.example.com
    RewriteEngine On
    RewriteRule ^ https://%{SERVER_NAME}%{REQUEST_URI} [END,R=301]
</VirtualHost>

<VirtualHost *:443>
    ServerName dmarc.example.com
    SSLEngine on
    SSLCertificateFile    /etc/ssl/dmarcula/fullchain.pem
    SSLCertificateKeyFile /etc/ssl/dmarcula/privkey.pem
    ProxyPreserveHost On
    ProxyPass        / http://127.0.0.1:8000/ timeout=120
    ProxyPassReverse / http://127.0.0.1:8000/
    RequestHeader set X-Forwarded-Proto "https"
    LimitRequestBody 62914560
</VirtualHost>
```

```bash
a2enmod ssl rewrite headers proxy proxy_http && apache2ctl configtest && systemctl restart apache2
sed -i -e 's|^BASE_URL=.*|BASE_URL=https://dmarc.example.com|' -e 's|^COOKIE_SECURE=.*|COOKIE_SECURE=true|' /opt/dmarcula/.env
systemctl restart dmarcula
```

**Docker (any OS):** `cp .env.example .env`, set `SECRET_KEY`, `ADMIN_PASSWORD` and `BASE_URL`,
then run `docker compose up -d --build`. It starts the app with PostgreSQL on port 8000.

**Windows Server:** install Python 3.12, Git and MariaDB, then `git clone` the repository and run
`py -3.12 -m venv .venv` and `.venv\Scripts\pip install -r requirements.txt`. Copy `.env.example`
to `.env` and edit it. Run `.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000` as a
service with [NSSM](https://nssm.cc/), and put IIS (URL Rewrite + ARR) in front with
[`deploy/iis/web.config`](../deploy/iis/web.config). The backup script is in [`deploy/windows/`](../deploy/windows).

**Manual install (other Linux):** the templates in [`deploy/`](../deploy) (systemd unit,
Apache / nginx vhost, cron jobs) contain `__PLACEHOLDERS__` and follow the same steps as
[`install.sh`](../deploy/install.sh), which documents each step in its comments.

## Requirements

- Python 3.10+ (the installer fetches 3.12 automatically if the system Python is older)
- MariaDB 10.5+ (or PostgreSQL / SQLite), 2 GB RAM for 100+ domains, about 2–8 GB of disk per year
- outbound HTTPS to `login.microsoftonline.com` / `graph.microsoft.com` and outbound DNS
- a single application process (the mailbox scheduler runs inside it)

## Troubleshooting

| Symptom | Fix |
|---|---|
| Domains show **no SPF / DMARC / DKIM** although they exist | The server's DNS has an internal copy of your zones (Active Directory). Keep `DNS_SERVERS=1.1.1.1,8.8.8.8` in `.env` (the default), restart, and run *Domény → Skontrolovať DNS*. Test with `dig @1.1.1.1 TXT _dmarc.example.com +short`. |
| Apache `500`, log shows `AH01144: No protocol handler` | `a2enmod proxy_http && systemctl restart apache2` |
| After enabling the site, **every** HTTPS site has an SSL error | Another vhost is bound to a host name or IP (`<VirtualHost syslog.example.com:443>`). Change all of them to `*:80` / `*:443`, and check with `apache2ctl -S`. |
| `apache2ctl configtest` fails | Nothing new is loaded until it passes. Typical causes: a missing certificate file, or a comment after a directive. |
| `502` / `503` from the web server | `systemctl status dmarcula`, `journalctl -u dmarcula -n 50`. On RHEL: `setsebool -P httpd_can_network_connect 1`. |
| Login keeps returning to the login page | `COOKIE_SECURE=true` while the site runs over plain HTTP. Enable HTTPS or set it to `false`. |
| SSO error after the Microsoft sign-in | `BASE_URL` must be the exact public `https://` URL, and the Entra redirect URI must be `BASE_URL/auth/oidc/callback`. |
| Forgotten admin password | `cd /opt/dmarcula && runuser -u dmarcula -- .venv/bin/python -m app.manage set-password admin` |
| Mailbox log shows `PendingRollbackError`, `server has gone away` or `Lost connection` | Fixed in 1.1.4: each report is stored in its own transaction, and a failing message no longer blocks the mailbox. Update. Root cause: `journalctl -u dmarcula --utc --since "…"`. The log in the UI is in UTC, `journalctl` uses local time unless you pass `--utc`. |
| Mailbox errors | *Administrácia → Poštové schránky → Log*, and [Microsoft 365 setup → troubleshooting](microsoft-365.md#troubleshooting) |

Backups, cron jobs, logs and maintenance commands: [Operations](operations.md).
