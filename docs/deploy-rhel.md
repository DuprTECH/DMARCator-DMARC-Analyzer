# Deploy on RHEL / Rocky Linux / AlmaLinux (Apache + MariaDB)

For **RHEL, Rocky Linux and AlmaLinux 9** (CentOS Stream 9 and Oracle Linux 9 work the same way).
On version 8, run `dnf module enable -y mariadb:10.5` before step 1.

Follow the steps from top to bottom and paste each block into the terminal. You only edit
**step 0**.

| Step | What happens |
|---|---|
| 0 | settings for this installation |
| 1 | install packages (`dnf install`) |
| 2 | configure MariaDB (database + user) |
| 3 | download the application (`git clone`) and its Python libraries |
| 4 | configure the application (`.env`) |
| 5 | run it as a service (systemd) |
| 6 | configure Apache, SELinux and the firewall |
| 7 | HTTPS certificate |
| 8 | cron jobs (backups, cleanup, alerts) |
| 9 | first login |

The application goes to **`/opt/dmarcula`**. With SELinux, systemd services may not execute
programs from home directories.

---

## Step 0 – settings

```bash
sudo -i

export DMARC_HOST=dmarc.example.com          # web address of DMARCula
export ADMIN_EMAIL=admin@example.com         # gets certificate and cron alerts
export APP_USER=dmarc
export APP_DIR=/opt/dmarcula
export DB_PASS="$(openssl rand -hex 24)"

echo "Database password: $DB_PASS"           # write it down
```

## Step 1 – install packages

```bash
dnf install -y git curl openssl cronie mariadb-server mariadb httpd mod_ssl
dnf install -y python3.12 python3.12-pip || dnf install -y python3.11 python3.11-pip
export PY="$(command -v python3.12 || command -v python3.11)"
$PY --version                                # 3.11 or 3.12

# certbot comes from EPEL
dnf install -y epel-release || dnf install -y "https://dl.fedoraproject.org/pub/epel/epel-release-latest-$(rpm -E %rhel).noarch.rpm"
dnf install -y certbot python3-certbot-apache
```

On RHEL with a subscription, EPEL also needs:
`subscription-manager repos --enable codeready-builder-for-rhel-9-$(arch)-rpms`.

## Step 2 – configure MariaDB

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

cat > /etc/my.cnf.d/90-dmarcula.cnf <<'EOF'
[mysqld]
innodb_buffer_pool_size = 512M
EOF
systemctl restart mariadb

mariadb -u dmarcula -p"$DB_PASS" -h 127.0.0.1 -e "SHOW DATABASES;"     # lists "dmarcula"
```

## Step 3 – download the application

```bash
id "$APP_USER" >/dev/null 2>&1 || useradd --create-home --shell /bin/bash "$APP_USER"
install -d -o "$APP_USER" -g "$APP_USER" -m 750 "$APP_DIR"

cd /tmp
runuser -u "$APP_USER" -- git clone https://github.com/DuprTECH/DMARCula---DMARC-Anylzer.git "$APP_DIR"

cd "$APP_DIR"
runuser -u "$APP_USER" -- $PY -m venv .venv
runuser -u "$APP_USER" -- .venv/bin/pip install --upgrade pip
runuser -u "$APP_USER" -- .venv/bin/pip install -r requirements.txt
```

## Step 4 – configure the application

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

runuser -u "$APP_USER" -- .venv/bin/python -c "from app.db import init_db; init_db(); print('database OK')"
```

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

## Step 6 – configure Apache, SELinux and the firewall

```bash
cd "$APP_DIR"
sed -e "s|__HOST__|$DMARC_HOST|g" -e "s|__PORT__|8000|g" \
    deploy/apache/dmarcula.conf > /etc/httpd/conf.d/dmarcula.conf

setsebool -P httpd_can_network_connect 1         # SELinux: allow Apache to reach 127.0.0.1:8000
apachectl configtest                             # "Syntax OK"
systemctl enable --now httpd
systemctl reload httpd

firewall-cmd --permanent --add-service=http --add-service=https
firewall-cmd --reload
```

## Step 7 – HTTPS certificate

**7a. Public DNS name: Let's Encrypt (automatic renewal)**

```bash
certbot --apache -d "$DMARC_HOST" --redirect --agree-tos -m "$ADMIN_EMAIL"
```

**7b. Internal name or company certificate:** put the certificate and key into
`/etc/pki/tls/certs/` and `/etc/pki/tls/private/` and use the HTTPS vhost from
[Installation reference → own certificate](installation.md#debian--ubuntu) (step 3b) in
`/etc/httpd/conf.d/dmarcula.conf`, with `/var/log/httpd` as the log directory.

**No HTTPS yet (testing only):**

```bash
runuser -u "$APP_USER" -- sed -i -e "s|^COOKIE_SECURE=.*|COOKIE_SECURE=false|" -e "s|^BASE_URL=https|BASE_URL=http|" "$APP_DIR/.env"
systemctl restart dmarcula
```

## Step 8 – cron jobs

```bash
cd "$APP_DIR"

install -d -o "$APP_USER" -g "$APP_USER" -m 750 /var/backups/dmarcula
printf '[client]\nuser=dmarcula\npassword=%s\nhost=127.0.0.1\n' "$DB_PASS" > .backup.my.cnf
chown "$APP_USER":"$APP_USER" .backup.my.cnf
chmod 600 .backup.my.cnf

sed -e "s|__USER__|$APP_USER|g" -e "s|__DIR__|$APP_DIR|g" -e "s|__MAILTO__|$ADMIN_EMAIL|g" \
    deploy/cron/dmarcula > /etc/cron.d/dmarcula
chmod 644 /etc/cron.d/dmarcula
systemctl enable --now crond

runuser -u "$APP_USER" -- bash -c "mariadb-dump --defaults-extra-file=$APP_DIR/.backup.my.cnf dmarcula | head -3"   # test
```

E-mail alerts need a local mail relay (`dnf install postfix` and configure `relayhost`).
Report fetching is not a cron job. The application does it itself every 15 minutes.

## Step 9 – first login

1. Open `https://dmarc.example.com` and log in as **admin** with the password from step 4.
2. Remove the bootstrap password:

   ```bash
   runuser -u "$APP_USER" -- sed -i "s|^ADMIN_PASSWORD=.*|ADMIN_PASSWORD=|" "$APP_DIR/.env"
   ```

3. Connect the report mailbox: *Administrácia → Poštové schránky → Exchange Online (Graph)*
   (see [Microsoft 365 setup](microsoft-365.md)), then click **Test** and **Spustiť**.

---

## Later

**Update:**

```bash
cd /opt/dmarcula
runuser -u dmarc -- git pull
runuser -u dmarc -- .venv/bin/pip install -r requirements.txt
systemctl restart dmarcula
```

| Command | Purpose |
|---|---|
| `systemctl status dmarcula` / `journalctl -u dmarcula -f` | service state / application log |
| `tail -f /var/log/httpd/error_log` | Apache errors (503 → service down or SELinux boolean missing) |
| `ausearch -m avc -ts recent` | recent SELinux denials |
| `cd /opt/dmarcula && runuser -u dmarc -- .venv/bin/python -m app.manage set-password admin` | reset the admin password |

**Problems:** see [Troubleshooting](installation.md#troubleshooting).
