# Deploy on Debian / Ubuntu (Apache + MariaDB)

For **Debian 12+** and **Ubuntu 22.04+**. Debian 11 and Ubuntu 20.04 ship a Python that is too old.

Follow the steps from top to bottom and paste each block into the terminal. You only edit
**step 0**. Everything else is copy & paste.

| Step | What happens |
|---|---|
| 0 | settings for this installation |
| 1 | install packages (`apt install`) |
| 2 | configure MariaDB (database + user) |
| 3 | download the application (`git clone`) and its Python libraries |
| 4 | configure the application (`.env`) |
| 5 | run it as a service (systemd) |
| 6 | configure Apache |
| 7 | HTTPS certificate |
| 8 | cron jobs (backups, cleanup, alerts) |
| 9 | first login |

What you need before you start:
- a server with root / sudo access,
- a DNS name (e.g. `dmarc.example.com`) pointing to the server,
- ports 80 and 443 reachable (for Let's Encrypt; otherwise see 7b).

---

## Step 0 – settings

Become root, then set the values for this server. **Edit the first two lines.**

```bash
sudo -i

export DMARC_HOST=dmarc.example.com          # web address of DMARCula
export ADMIN_EMAIL=admin@example.com         # gets certificate and cron alerts
export APP_USER=dmarc                        # Linux user the app runs as
export APP_DIR=/home/dmarc/dmarcula          # application directory – NOT public_html
export DB_PASS="$(openssl rand -hex 24)"     # database password (generated)

echo "Database password: $DB_PASS"           # write it down
```

> If you close the terminal between steps, run `sudo -i` and the `export` lines again, but keep
> the same `DB_PASS`: `export DB_PASS=<the password you wrote down>`.

## Step 1 – install packages

```bash
apt update
apt install -y python3 python3-venv python3-pip git curl openssl cron \
               mariadb-server mariadb-client \
               apache2 certbot python3-certbot-apache

python3 --version          # must be 3.10 or newer
```

## Step 2 – configure MariaDB

Create the database and its user:

```bash
systemctl enable --now mariadb

mariadb <<SQL
CREATE DATABASE dmarcula CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'dmarcula'@'localhost' IDENTIFIED BY '${DB_PASS}';
CREATE USER 'dmarcula'@'127.0.0.1' IDENTIFIED BY '${DB_PASS}';
GRANT ALL PRIVILEGES ON dmarcula.* TO 'dmarcula'@'localhost';
GRANT ALL PRIVILEGES ON dmarcula.* TO 'dmarcula'@'127.0.0.1';
FLUSH PRIVILEGES;
SQL
```

Give MariaDB more memory for 100+ domains:

```bash
cat > /etc/mysql/mariadb.conf.d/90-dmarcula.cnf <<'EOF'
[mysqld]
innodb_buffer_pool_size = 512M
EOF
systemctl restart mariadb
```

Check that it works:

```bash
mariadb -u dmarcula -p"$DB_PASS" -h 127.0.0.1 -e "SHOW DATABASES;"     # lists "dmarcula"
```

## Step 3 – download the application

Create the system user, download the code and install the Python libraries:

```bash
id "$APP_USER" >/dev/null 2>&1 || useradd --create-home --shell /bin/bash "$APP_USER"

cd /tmp
runuser -u "$APP_USER" -- git clone https://github.com/DuprTECH/DMARCula---DMARC-Anylzer.git "$APP_DIR"

cd "$APP_DIR"
runuser -u "$APP_USER" -- python3 -m venv .venv
runuser -u "$APP_USER" -- .venv/bin/pip install --upgrade pip
runuser -u "$APP_USER" -- .venv/bin/pip install -r requirements.txt
```

## Step 4 – configure the application

Create `.env` with a random secret key, the database connection and a first admin password:

```bash
cd "$APP_DIR"
export ADMIN_PASSWORD="Admin-$(openssl rand -hex 6)"

runuser -u "$APP_USER" -- cp .env.example .env
chmod 600 .env
runuser -u "$APP_USER" -- sed -i \
  -e "s|^SECRET_KEY=.*|SECRET_KEY=$(openssl rand -hex 32)|" \
  -e "s|^BASE_URL=.*|BASE_URL=https://${DMARC_HOST}|" \
  -e "s|^COOKIE_SECURE=.*|COOKIE_SECURE=true|" \
  -e "s|^# DATABASE_URL=mysql.*|DATABASE_URL=mysql+pymysql://dmarcula:${DB_PASS}@127.0.0.1:3306/dmarcula|" \
  -e "s|^ADMIN_PASSWORD=.*|ADMIN_PASSWORD=${ADMIN_PASSWORD}|" \
  .env

echo "Web login: admin / $ADMIN_PASSWORD"      # write it down
```

Create the tables and check the database connection:

```bash
runuser -u "$APP_USER" -- .venv/bin/python -c "from app.db import init_db; init_db(); print('database OK')"
```

LDAP / SSO login can be added later in the same file (see [Authentication](authentication.md)).

## Step 5 – run as a service

```bash
cd "$APP_DIR"
sed -e "s|__USER__|$APP_USER|g" -e "s|__DIR__|$APP_DIR|g" -e "s|__PORT__|8000|g" \
    deploy/systemd/dmarcula.service > /etc/systemd/system/dmarcula.service

systemctl daemon-reload
systemctl enable --now dmarcula

sleep 3
systemctl status dmarcula --no-pager             # "active (running)"
curl -s http://127.0.0.1:8000/healthz            # {"status":"ok"}
```

If it is not running, run `journalctl -u dmarcula -n 50`. The last lines show the reason.

## Step 6 – configure Apache

```bash
cd "$APP_DIR"
a2enmod proxy proxy_http headers ssl
sed -e "s|__HOST__|$DMARC_HOST|g" -e "s|__PORT__|8000|g" \
    deploy/apache/dmarcula.conf > /etc/apache2/sites-available/dmarcula.conf

a2ensite dmarcula
apache2ctl configtest                            # "Syntax OK"
systemctl reload apache2

# firewall (only if ufw is active)
ufw status | grep -q active && ufw allow 'Apache Full'
```

> **Already have a site for this name**, e.g. with `DocumentRoot /home/dmarc/public_html`?
> Don't create a second one. Add the proxy lines from
> [`deploy/apache/dmarcula.conf`](../deploy/apache/dmarcula.conf) into that existing vhost
> (both `:80` and `:443`) and empty `public_html`. Details are in
> [Installation reference → existing site](installation.md#existing-site-with-documentroot-homedmarcpublic_html).

## Step 7 – HTTPS certificate

**7a. Public DNS name: Let's Encrypt (automatic renewal)**

```bash
certbot --apache -d "$DMARC_HOST" --redirect --agree-tos -m "$ADMIN_EMAIL"
```

**7b. Internal name or company certificate:** follow
[Installation reference → own certificate](installation.md#debian--ubuntu), step 3b.

**No HTTPS yet (testing only):** allow login over plain HTTP:

```bash
runuser -u "$APP_USER" -- sed -i -e "s|^COOKIE_SECURE=.*|COOKIE_SECURE=false|" -e "s|^BASE_URL=https|BASE_URL=http|" "$APP_DIR/.env"
systemctl restart dmarcula
```

## Step 8 – cron jobs

Backups (daily, 30 days kept), deleting reports older than 2 years (weekly), and an e-mail
alert when a mailbox stops working (every 30 min):

```bash
cd "$APP_DIR"

# backup folder and database credentials for the backup job
install -d -o "$APP_USER" -g "$APP_USER" -m 750 /var/backups/dmarcula
printf '[client]\nuser=dmarcula\npassword=%s\nhost=127.0.0.1\n' "$DB_PASS" > .backup.my.cnf
chown "$APP_USER":"$APP_USER" .backup.my.cnf
chmod 600 .backup.my.cnf

# the jobs
sed -e "s|__USER__|$APP_USER|g" -e "s|__DIR__|$APP_DIR|g" -e "s|__MAILTO__|$ADMIN_EMAIL|g" \
    deploy/cron/dmarcula > /etc/cron.d/dmarcula
chmod 644 /etc/cron.d/dmarcula
systemctl enable --now cron
```

Test the backup command once:

```bash
runuser -u "$APP_USER" -- bash -c "mariadb-dump --defaults-extra-file=$APP_DIR/.backup.my.cnf dmarcula | head -3"
```

What the jobs do is described in [`deploy/cron/dmarcula`](../deploy/cron/dmarcula) and in
[Operations](operations.md#cron-jobs). E-mail alerts need a working local mail relay
(`apt install postfix` → "Satellite system"). Without one, the jobs still run.

**Report fetching is not a cron job.** The application fetches mail itself every 15 minutes.

## Step 9 – first login

1. Open `https://dmarc.example.com` and log in as **admin** with the password from step 4.
2. Remove the bootstrap password from `.env`. It's only used for the very first start:

   ```bash
   runuser -u "$APP_USER" -- sed -i "s|^ADMIN_PASSWORD=.*|ADMIN_PASSWORD=|" "$APP_DIR/.env"
   ```

3. Connect the report mailbox: *Administrácia → Poštové schránky → Exchange Online (Graph)*.
   See [Microsoft 365 setup](microsoft-365.md) for the app registration in Entra ID.
4. Click **Test**, then **Spustiť**. The first domains appear on the dashboard within minutes.

---

## Later

**Update to a new version:**

```bash
cd /home/dmarc/dmarcula
runuser -u dmarc -- git pull
runuser -u dmarc -- .venv/bin/pip install -r requirements.txt
systemctl restart dmarcula
```

**Useful commands:**

| Command | Purpose |
|---|---|
| `systemctl status dmarcula` | is it running? |
| `journalctl -u dmarcula -f` | application log |
| `tail -f /var/log/apache2/error.log` | Apache errors (e.g. 503) |
| `cd /home/dmarc/dmarcula && runuser -u dmarc -- .venv/bin/python -m app.manage status` | mailbox status |
| `cd /home/dmarc/dmarcula && runuser -u dmarc -- .venv/bin/python -m app.manage set-password admin` | reset the admin password |
| `ls -lh /var/backups/dmarcula` | backups |

**Problems:** see [Troubleshooting](installation.md#troubleshooting).
