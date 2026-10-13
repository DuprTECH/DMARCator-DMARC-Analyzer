# Installation

**Step-by-step guides, copy & paste from top to bottom:**

| System | Guide |
|---|---|
| Debian 12+ / Ubuntu 22.04+ | [deploy-debian.md](deploy-debian.md) |
| RHEL / Rocky / AlmaLinux 8–9 | [deploy-rhel.md](deploy-rhel.md) |
| openSUSE Leap / SLES 15 | [deploy-suse.md](deploy-suse.md) |
| Windows Server 2019+ (IIS) | [deploy-windows.md](deploy-windows.md) |
| Any OS with Docker | [Docker Compose](#docker-compose) |
| Other Linux (Arch, Alpine, …) | [Other distributions](#other-distributions) |

Each guide follows the same order: **packages → database → `git clone` → `.env` → service →
web server → HTTPS → cron → first login.** The configuration files the guides use are in
[`deploy/`](../deploy) (systemd unit, Apache / nginx / IIS configs, cron jobs).

**Automatic alternative (Linux):** [`deploy/install.sh`](../deploy/install.sh) performs the
same steps after asking a few questions:

```bash
curl -fsSL https://raw.githubusercontent.com/DuprTECH/DMARCula---DMARC-Anylzer/main/deploy/install.sh -o install.sh
sudo bash install.sh
```

**Private repository:** the `curl` download needs public access. Instead, do steps 0, 1 and 3
(deploy key + `git clone`) of [deploy-debian.md](deploy-debian.md) / [deploy-rhel.md](deploy-rhel.md),
then run the installer from the clone: `sudo bash /home/dmarc/dmarcula/deploy/install.sh`
(or `/opt/dmarcula/...` on RHEL). It uses the existing clone and does the remaining steps.

The rest of this page is reference material: how it runs, sizing, web server variants,
hosting panels, Docker, updating and troubleshooting.

- [How it runs](#how-it-runs)
- [Requirements and sizing](#requirements-and-sizing)
- [Apache reference](#apache-reference) (Let's Encrypt, own certificate, existing `public_html` vhost, EL)
- [nginx instead of Apache](#nginx-instead-of-apache)
- [Hosting panels and `public_html`](#hosting-panels-and-public_html)
- [Docker Compose](#docker-compose)
- [Other distributions](#other-distributions)
- [Updating](#updating)
- [Troubleshooting](#troubleshooting)

## How it runs

DMARCula is a Python (FastAPI) application. Unlike a PHP site, it does **not** live in a web
root. It runs as a long-lived process (a systemd service) that serves the UI on
`127.0.0.1:8000` and also polls the report mailbox every few minutes. Your web server
(nginx or Apache) terminates HTTPS and forwards requests to it:

```
browser ──HTTPS──► nginx / Apache :443 ──HTTP──► DMARCula (uvicorn) 127.0.0.1:8000 ──► MariaDB
                                                     └── every 15 min: Exchange Online / IMAP mailbox
```

> **Never put the application into `public_html` or any other web root.** Everything there can
> be downloaded over HTTP, including `.env` (secret key, LDAP and database passwords) and the
> source code. Install it next to the web root, for example `/home/dmarc/dmarcula`, or under `/opt/dmarcula`.

Run **one** application process only (no `--workers N`, no multiple replicas). The scheduler that
polls mailboxes runs inside the process.

## Requirements and sizing

| | Minimum | 100–200 domains |
|---|---|---|
| Python | 3.10+ | 3.11 / 3.12 |
| CPU / RAM | 1 vCPU / 1 GB | 2 vCPU / 2–4 GB |
| Database | SQLite (built in) | **MariaDB 10.6+** (or PostgreSQL 14+) |
| Disk | 1 GB | ~2–8 GB per year of reports (less with [pruning](operations.md#data-retention)) |

Rough data volume: each domain that sends mail receives about 2–6 aggregate reports per day.
With 140 domains, that's about 500–800 reports and 5–20 thousand records per day, or a few
million records per year. SQLite copes with that, but MariaDB gives you concurrent access,
standard backups and room to grow.

Network access needed from the server:

- outbound HTTPS to `login.microsoftonline.com` and `graph.microsoft.com` (or your IMAP server),
- DNS (UDP/TCP 53) for SPF, DKIM and DMARC checks, and outbound HTTPS to `mta-sts.<domain>`,
- inbound HTTPS from your users.

Users' browsers load the chart library from `cdn.jsdelivr.net`
(see [offline environments](#troubleshooting)).

## Apache reference

DMARCula needs its **own host name** (e.g. `dmarc.example.com`). It can't be served from a
sub-path like `https://example.com/dmarc/`. Apache only forwards requests to
`127.0.0.1:8000` and doesn't serve any files itself.

### Debian / Ubuntu

**1. Install and enable modules**

```bash
sudo apt install -y apache2 certbot python3-certbot-apache
sudo a2enmod proxy proxy_http headers ssl rewrite http2
sudo systemctl restart apache2
```

**2. HTTP virtual host.** Certbot needs it to obtain the certificate, and afterwards it
redirects to HTTPS:

```bash
sudo tee /etc/apache2/sites-available/dmarcula.conf >/dev/null <<'EOF'
<VirtualHost *:80>
    ServerName dmarc.example.com

    ProxyPreserveHost On
    ProxyRequests Off
    ProxyPass        / http://127.0.0.1:8000/ timeout=120
    ProxyPassReverse / http://127.0.0.1:8000/

    ErrorLog  ${APACHE_LOG_DIR}/dmarcula-error.log
    CustomLog ${APACHE_LOG_DIR}/dmarcula-access.log combined
</VirtualHost>
EOF

sudo a2ensite dmarcula
sudo apache2ctl configtest && sudo systemctl reload apache2
```

**3a. HTTPS with Let's Encrypt (public DNS name).** Certbot creates
`/etc/apache2/sites-available/dmarcula-le-ssl.conf` with the proxy settings, adds the
HTTP → HTTPS redirect, and renews the certificate automatically through a systemd timer:

```bash
sudo certbot --apache -d dmarc.example.com --redirect
```

Then add the forwarded-protocol header and HSTS to the generated HTTPS vhost (inside
`<VirtualHost *:443>`):

```bash
sudo sed -i 's|ProxyPassReverse / http://127.0.0.1:8000/|&\n    RequestHeader set X-Forwarded-Proto "https"\n    Header always set Strict-Transport-Security "max-age=31536000"\n    LimitRequestBody 62914560|' \
  /etc/apache2/sites-available/dmarcula-le-ssl.conf
sudo apache2ctl configtest && sudo systemctl reload apache2
```

**3b. HTTPS with your own certificate (internal CA / commercial).** Replace the whole file
instead of running certbot:

```bash
sudo tee /etc/apache2/sites-available/dmarcula.conf >/dev/null <<'EOF'
<VirtualHost *:80>
    ServerName dmarc.example.com
    RewriteEngine On
    RewriteRule ^ https://%{SERVER_NAME}%{REQUEST_URI} [END,R=301]
</VirtualHost>

<VirtualHost *:443>
    ServerName dmarc.example.com
    Protocols h2 http/1.1

    SSLEngine on
    SSLCertificateFile    /etc/ssl/dmarcula/fullchain.pem    # certificate + intermediates
    SSLCertificateKeyFile /etc/ssl/dmarcula/privkey.pem

    ProxyPreserveHost On
    ProxyRequests Off
    ProxyPass        / http://127.0.0.1:8000/ timeout=120
    ProxyPassReverse / http://127.0.0.1:8000/
    RequestHeader set X-Forwarded-Proto "https"

    Header always set Strict-Transport-Security "max-age=31536000"
    LimitRequestBody 62914560          # 60 MB – manual report uploads

    ErrorLog  ${APACHE_LOG_DIR}/dmarcula-error.log
    CustomLog ${APACHE_LOG_DIR}/dmarcula-access.log combined
</VirtualHost>
EOF

sudo install -d -m 750 /etc/ssl/dmarcula
# copy fullchain.pem and privkey.pem there, then:
sudo chmod 640 /etc/ssl/dmarcula/privkey.pem
sudo apache2ctl configtest && sudo systemctl reload apache2
```

**4. Finish:** set `COOKIE_SECURE=true` and `BASE_URL=https://dmarc.example.com` in `.env`,
then `sudo systemctl restart dmarcula`. Open the firewall if needed: `sudo ufw allow 'Apache Full'`.

**Check:**

```bash
curl -sI https://dmarc.example.com/login | head -5          # HTTP/2 200
curl -s  https://dmarc.example.com/healthz                  # {"status":"ok"}
sudo tail -f /var/log/apache2/dmarcula-error.log            # proxy errors (503 = app not running)
```

### Optional: allow only the internal network

DMARCula has its own login, but you can also hide it from the internet. Add this inside the
`<VirtualHost *:443>` block:

```apache
<Location />
    Require ip 10.0.0.0/8 192.168.0.0/16 203.0.113.10
</Location>
```

If you use SSO, the browser only needs to reach Entra ID, so the app may stay internal-only.
Microsoft never connects to the app.

### Existing site with `DocumentRoot /home/dmarc/public_html`

If the host name already has a vhost (e.g. created by a hosting panel) whose document root is
`/home/dmarc/public_html`, you don't need a new vhost. Add the proxy lines **inside the
existing `<VirtualHost *:443>`** (and the `*:80` one, or redirect it to HTTPS):

```apache
    ProxyPreserveHost On
    ProxyRequests Off
    ProxyPass        / http://127.0.0.1:8000/ timeout=120
    ProxyPassReverse / http://127.0.0.1:8000/
    RequestHeader set X-Forwarded-Proto "https"
```

`ProxyPass /` takes precedence over the document root, so nothing from `public_html` is served
any more. Empty the folder anyway. On panels, put these lines into the panel's "custom Apache
directives" field for the site, otherwise the panel overwrites manual edits.

### RHEL / Rocky / AlmaLinux

```bash
sudo dnf install -y httpd mod_ssl
sudo setsebool -P httpd_can_network_connect 1        # SELinux: allow Apache to connect to 127.0.0.1:8000
sudo systemctl enable --now httpd
```

The configuration is the same as above, but:
- the file goes to `/etc/httpd/conf.d/dmarcula.conf` (no `a2ensite`; every `*.conf` there is loaded),
- `${APACHE_LOG_DIR}` → `/var/log/httpd`,
- `mod_proxy`, `mod_headers`, `mod_rewrite` and `mod_http2` are enabled by default,
- check with `sudo apachectl configtest`, reload with `sudo systemctl reload httpd`,
- Let's Encrypt: `sudo dnf install -y epel-release && sudo dnf install -y certbot python3-certbot-apache`,
- firewall: `sudo firewall-cmd --permanent --add-service=http --add-service=https && sudo firewall-cmd --reload`.

### Common Apache problems

| Symptom | Fix |
|---|---|
| `503 Service Unavailable` | The app isn't running: `systemctl status dmarcula`. On EL, also run `setsebool -P httpd_can_network_connect 1`. |
| `Invalid command 'ProxyPass'` | `sudo a2enmod proxy proxy_http` |
| `Invalid command 'RequestHeader'` / `'Header'` | `sudo a2enmod headers` |
| Login loops back to the login page | `COOKIE_SECURE=true` while the site is served over plain HTTP. Enable HTTPS, or set `false` temporarily. |
| SSO error after Microsoft login | `BASE_URL` must be the exact public `https://` URL, and the redirect URI in Entra ID must match it. |
| Upload fails with `413` | Raise `LimitRequestBody` (bytes). |

## nginx instead of Apache

In step 6 of the Linux guides, use the nginx template instead of the Apache one:

```bash
apt install -y nginx certbot python3-certbot-nginx          # dnf / zypper on other systems
cd "$APP_DIR"
sed -e "s|__HOST__|$DMARC_HOST|g" -e "s|__PORT__|8000|g" deploy/nginx/dmarcula.conf \
    > /etc/nginx/sites-available/dmarcula                   # EL: /etc/nginx/conf.d/dmarcula.conf, SUSE: /etc/nginx/vhosts.d/dmarcula.conf
ln -s /etc/nginx/sites-available/dmarcula /etc/nginx/sites-enabled/     # Debian/Ubuntu only
nginx -t && systemctl reload nginx
certbot --nginx -d "$DMARC_HOST" --redirect
```

On EL, also run `setsebool -P httpd_can_network_connect 1`.

## Hosting panels and `public_html`

If the server is managed by a panel (Virtualmin, ISPConfig, HestiaCP, CloudPanel…) and the
site's document root is `/home/dmarc/public_html`, follow these rules:

1. Install the application **outside** `public_html` (e.g. `/home/dmarc/dmarcula`) with steps 1–5 of the guide for your system.
2. Run it as a systemd service, which needs root once. Most panels let the admin add custom
   services. Without root and without systemd, the app cannot run reliably, because the
   mailbox polling stops whenever the process is not running.
3. Point the website at the app with a reverse proxy:
   - **Panels with a proxy option** (Virtualmin *Proxy paths*, HestiaCP / CloudPanel *reverse
     proxy* templates, ISPConfig *Apache directives*): proxy `/` to `http://127.0.0.1:8000/`.
   - **Apache with only `.htaccess` available**: put this into `public_html/.htaccess`. It
     requires `mod_proxy`, `mod_proxy_http` and `mod_rewrite`, which the server admin must
     enable:

     ```apache
     RewriteEngine On
     RewriteRule ^(.*)$ http://127.0.0.1:8000/$1 [P,L]
     ```
4. Leave nothing else in `public_html`.

Shared hosting with "Setup Python App" (cPanel / Passenger) is **not** supported. Passenger
stops idle apps, so the mailbox scheduler doesn't run.

## Docker Compose

Docker Compose runs the app together with PostgreSQL:

```bash
sudo apt install -y docker.io docker-compose-plugin     # or Docker CE from docker.com
git clone https://github.com/DuprTECH/DMARCula---DMARC-Anylzer.git dmarcula && cd dmarcula
cp .env.example .env          # set SECRET_KEY, ADMIN_PASSWORD, BASE_URL, POSTGRES_PASSWORD
docker compose up -d --build
```

To use an existing MariaDB instead, remove the `db` service from `docker-compose.yml` and set
`DATABASE_URL=mysql+pymysql://dmarcula:…@<host>:3306/dmarcula` in `.env`. Then put the same
nginx or Apache proxy in front of port 8000.

---

## Other distributions

The steps of [deploy-debian.md](deploy-debian.md) apply to any systemd-based Linux. Only the
package names and paths differ:

| Distribution | Packages (step 1) | Notes |
|---|---|---|
| Arch Linux | `pacman -S python git mariadb apache certbot certbot-apache cronie` | before step 2: `mariadb-install-db --user=mysql --basedir=/usr --datadir=/var/lib/mysql`; enable `mod_proxy`, `mod_proxy_http`, `mod_headers` in `/etc/httpd/conf/httpd.conf` and `Include conf/extra/dmarcula.conf` |
| Fedora | `dnf install python3 git mariadb-server httpd mod_ssl cronie certbot python3-certbot-apache` | as [RHEL](deploy-rhel.md), no EPEL needed |
| Alpine (OpenRC, no systemd) | `apk add python3 py3-pip git mariadb mariadb-client apache2 apache2-proxy certbot` | `/etc/init.d/mariadb setup`; run the app with an OpenRC service instead of systemd, or use Docker |
| Any distro with Python < 3.10 | install [uv](https://docs.astral.sh/uv/), then in step 3 use `uv venv --seed --python 3.12 .venv` | uv downloads a private Python 3.12 |

## Updating

```bash
cd /home/dmarc/dmarcula
sudo -u dmarc git pull
sudo -u dmarc .venv/bin/pip install -r requirements.txt
sudo systemctl restart dmarcula
```

Schema changes are applied automatically at startup: new tables, columns and indexes are
created and new columns are backfilled. Back up the database before upgrading.

Backups, data retention, monitoring and logs: see [Operations](operations.md).

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Service fails: `SECRET_KEY must be set` | Set `SECRET_KEY` (16+ characters) in `.env`. |
| `Access denied for user 'dmarcula'@'localhost'` | Wrong password in `DATABASE_URL`, or the user was created for a different host. Special characters in the password must be URL-encoded. |
| `ModuleNotFoundError: pymysql` | Run `pip install -r requirements.txt` inside `.venv`. |
| `SyntaxError` / `TypeError: unsupported operand type(s) for \|` at startup | Python is older than 3.10. |
| nginx `502 Bad Gateway` | Service not running (`systemctl status dmarcula`). On EL, run `setsebool -P httpd_can_network_connect 1`. |
| Cannot log in after the first start | `ADMIN_PASSWORD` was empty on the first start. Set it, drop and recreate the (empty) database, and restart. |
| DNS checks are slow | The first system resolver doesn't answer. Set `DNS_SERVERS=1.1.1.1,8.8.8.8` or use internal resolvers. |
| SSO: "SSO prihlásenie zlyhalo" | The redirect URI must be exactly `BASE_URL` + `/auth/oidc/callback`. |
| Mailbox errors | See [Microsoft 365 setup → troubleshooting](microsoft-365.md#troubleshooting). Details are in *Administrácia → Poštové schránky → Log*. |
| No charts in an offline network | Download `https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js` to `app/static/js/` and point the `<script>` in `app/templates/base.html` to `/static/js/echarts.min.js`. The world map stays unavailable offline. |
