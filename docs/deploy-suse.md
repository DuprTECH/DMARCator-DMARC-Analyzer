# Deploy on openSUSE Leap / SLES 15 (Apache + MariaDB)

For **openSUSE Leap 15.5+** and **SLES 15 SP5+**. On SLES, enable the *Python 3* and
*Web and Scripting* modules first (`SUSEConnect -p sle-module-python3/15.5/x86_64`, …).

Paste each block in order. You only edit **step 0**.

| Step | What happens |
|---|---|
| 0 | settings |
| 1 | install packages (`zypper install`) |
| 2 | configure MariaDB |
| 3 | `git clone` + Python libraries |
| 4 | configure the application (`.env`) |
| 5 | systemd service |
| 6 | Apache and the firewall |
| 7 | HTTPS certificate |
| 8 | cron jobs |
| 9 | first login |

---

## Step 0 – settings

```bash
sudo -i

export DMARC_HOST=dmarc.example.com
export ADMIN_EMAIL=admin@example.com
export APP_USER=dmarc
export APP_DIR=/home/dmarc/dmarcula
export DB_PASS="$(openssl rand -hex 24)"

echo "Database password: $DB_PASS"           # write it down
```

## Step 1 – install packages

```bash
zypper refresh
zypper install -y python311 python311-pip git openssh-clients curl openssl bind-utils cron mariadb mariadb-client apache2
zypper install -y certbot python3-certbot-apache        # optional, for Let's Encrypt
python3.11 --version
```

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

mariadb -u dmarcula -p"$DB_PASS" -h 127.0.0.1 -e "SHOW DATABASES;"
```

## Step 3 – download the application

```bash
id "$APP_USER" >/dev/null 2>&1 || useradd --create-home --shell /bin/bash "$APP_USER"

cd /tmp
runuser -u "$APP_USER" -- git clone https://github.com/DuprTECH/DMARCula---DMARC-Anylzer.git "$APP_DIR"

cd "$APP_DIR"
runuser -u "$APP_USER" -- python3.11 -m venv .venv
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

echo "Web login: admin / $ADMIN_PASSWORD"

runuser -u "$APP_USER" -- .venv/bin/python -c "from app.db import init_db; init_db(); print('database OK')"
```

Check that the server sees your **public** DNS records. `DNS_SERVERS=1.1.1.1,8.8.8.8` from
`.env.example` is used by the app; this also tests that outbound DNS is allowed:

```bash
dig +short @1.1.1.1 TXT _dmarc.example.com            # your real domain – must print v=DMARC1…
grep ^DNS_SERVERS .env
```

If outbound DNS to the internet is blocked, set `DNS_SERVERS` to an internal resolver that
answers with the **public** view of your domains (not the Active Directory zone).

## Step 5 – run as a service

```bash
cd "$APP_DIR"
sed -e "s|__USER__|$APP_USER|g" -e "s|__DIR__|$APP_DIR|g" -e "s|__PORT__|8000|g" \
    deploy/systemd/dmarcula.service > /etc/systemd/system/dmarcula.service
systemctl daemon-reload
systemctl enable --now dmarcula
sleep 3; curl -s http://127.0.0.1:8000/healthz          # {"status":"ok"}
```

## Step 6 – Apache and the firewall

```bash
cd "$APP_DIR"
for m in proxy proxy_http headers ssl; do a2enmod $m; done
sed -e "s|__HOST__|$DMARC_HOST|g" -e "s|__PORT__|8000|g" \
    deploy/apache/dmarcula.conf > /etc/apache2/vhosts.d/dmarcula.conf

apachectl configtest
systemctl enable --now apache2
systemctl restart apache2                        # restart (not reload) loads the new modules

firewall-cmd --permanent --add-service=http --add-service=https
firewall-cmd --reload
```

## Step 7 – HTTPS certificate

```bash
a2enflag SSL                                    # SUSE: let Apache listen on 443
certbot --apache -d "$DMARC_HOST" --redirect --agree-tos -m "$ADMIN_EMAIL"
systemctl restart apache2
```

For a company certificate, use the HTTPS vhost from
[Installation reference → own certificate](installation.md#debian--ubuntu) (step 3b) in
`/etc/apache2/vhosts.d/dmarcula.conf`, with log paths under `/var/log/apache2/`.

## Step 8 – cron jobs

```bash
cd "$APP_DIR"
install -d -o "$APP_USER" -g "$APP_USER" -m 750 /var/backups/dmarcula
printf '[client]\nuser=dmarcula\npassword=%s\nhost=127.0.0.1\n' "$DB_PASS" > .backup.my.cnf
chown "$APP_USER":"$APP_USER" .backup.my.cnf; chmod 600 .backup.my.cnf

sed -e "s|__USER__|$APP_USER|g" -e "s|__DIR__|$APP_DIR|g" -e "s|__MAILTO__|$ADMIN_EMAIL|g" \
    deploy/cron/dmarcula > /etc/cron.d/dmarcula
chmod 644 /etc/cron.d/dmarcula
systemctl enable --now cron
```

## Step 9 – first login

1. Open `https://dmarc.example.com`, then log in as **admin** with the password from step 4.
2. `runuser -u "$APP_USER" -- sed -i "s|^ADMIN_PASSWORD=.*|ADMIN_PASSWORD=|" "$APP_DIR/.env"`
3. Connect the mailbox: *Administrácia → Poštové schránky* (see [Microsoft 365 setup](microsoft-365.md)).

**Update later:** `cd /home/dmarc/dmarcula && runuser -u dmarc -- git pull && runuser -u dmarc -- .venv/bin/pip install -r requirements.txt && systemctl restart dmarcula`
