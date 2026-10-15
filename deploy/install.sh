#!/usr/bin/env bash
# DMARCator installer and updater for Linux servers with systemd.
#
#   curl -fsSL https://raw.githubusercontent.com/DuprTECH/DMARCator-DMARC-Analyzer/main/deploy/install.sh -o install.sh
#   sudo bash install.sh
#
# Supported: Debian 11+, Ubuntu 20.04+, RHEL / Rocky / AlmaLinux / CentOS Stream 8+, Fedora, openSUSE / SLES 15.
# Running it again on an installed server updates the application.
#
# Every question can be answered in advance with an environment variable (for unattended installs):
#   DMARC_HOST=dmarc.example.com  WEB=apache|nginx|none  LETSENCRYPT=yes|no  LE_EMAIL=...
#   ADMIN_PASSWORD=...  ALERT_EMAIL=...  APP_USER=dmarcator  APP_DIR=/opt/dmarcator  APP_PORT=8000  ASSUME_YES=1
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/DuprTECH/DMARCator-DMARC-Analyzer.git}"
BRANCH="${BRANCH:-main}"
DB_NAME="${DB_NAME:-dmarcator}"
DB_USER="${DB_USER:-dmarcator}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/dmarcator}"

# ------------------------------------------------------------------ helpers
if [[ -t 1 ]]; then B=$'\e[1m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; N=$'\e[0m'; else B=; G=; Y=; R=; N=; fi
step() { printf '\n%s==> %s%s\n' "$G$B" "$*" "$N"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '%s!!  %s%s\n' "$Y" "$*" "$N" >&2; }
die()  { printf '%sERROR: %s%s\n' "$R" "$*" "$N" >&2; exit 1; }

tty_ok() { [[ "${ASSUME_YES:-0}" != 1 ]] && { true </dev/tty; } 2>/dev/null; }

# ask VAR "question" "default" – keeps VAR if it is already set in the environment
ask() {
    local var=$1 question=$2 default=${3-} answer=""
    [[ -n "${!var-}" ]] && return 0
    if tty_ok; then read -r -p "    $question [${default}]: " answer </dev/tty || true; fi
    printf -v "$var" '%s' "${answer:-$default}"
}

ask_secret() {
    local var=$1 question=$2 answer=""
    [[ -n "${!var-}" ]] && return 0
    if tty_ok; then read -r -s -p "    $question: " answer </dev/tty || true; echo; fi
    printf -v "$var" '%s' "$answer"
}

as_app() { runuser -u "$APP_USER" -- "$@"; }

# set KEY=VALUE in the app's .env (value single-quoted, comments and other lines preserved)
env_set() {
    KEY="$1" VALUE="$2" ENV_FILE="$APP_DIR/.env" python3 - <<'PY'
import os, re
path, key, value = os.environ["ENV_FILE"], os.environ["KEY"], os.environ["VALUE"]
lines = open(path, encoding="utf-8").read().splitlines()
new = f"{key}='{value}'" if value else f"{key}="
for i, line in enumerate(lines):
    if re.match(rf"^#?\s*{key}=", line):
        lines[i] = new
        break
else:
    lines.append(new)
open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
PY
}

env_get() {
    KEY="$1" ENV_FILE="$APP_DIR/.env" python3 - <<'PY'
import os, re
for line in open(os.environ["ENV_FILE"], encoding="utf-8"):
    m = re.match(rf"^{os.environ['KEY']}=(.*)$", line.strip())
    if m:
        print(re.sub(r"\s+#.*$", "", m.group(1)).strip().strip("'\""))
        break
PY
}

wait_healthy() {
    for _ in $(seq 1 40); do
        curl -fsS -m 3 "http://127.0.0.1:${APP_PORT}/healthz" >/dev/null 2>&1 && return 0
        sleep 1
    done
    return 1
}

port_free() { ! (ss -ltnH 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$1\$"); }

# create .venv in the current directory (APP_DIR) and install the requirements
make_venv() {
    local py="" c
    for c in python3.13 python3.12 python3.11 python3.10 python3; do
        if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys, venv; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then py="$c"; break; fi
    done
    if [[ -n "$py" ]]; then
        info "Using $($py --version)"
        [[ -x .venv/bin/python ]] || as_app "$py" -m venv .venv
    else
        info "System Python is older than 3.10 – installing Python 3.12 with uv"
        [[ -x /usr/local/bin/uv ]] || curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh >/dev/null
        as_app env UV_PYTHON_INSTALL_DIR="$APP_DIR/.python" UV_CACHE_DIR="$APP_DIR/.cache/uv" /usr/local/bin/uv venv --seed --python 3.12 .venv
    fi
    as_app .venv/bin/python -m pip install --quiet --upgrade pip
    as_app .venv/bin/python -m pip install --quiet -r requirements.txt
    info "Python packages installed."
}

# /etc/systemd/system/dmarcator.service from the template (APP_USER, APP_DIR, APP_PORT)
render_unit() {
    sed -e "s|__USER__|$APP_USER|g" -e "s|__DIR__|$APP_DIR|g" -e "s|__PORT__|$APP_PORT|g" \
        "$APP_DIR/deploy/systemd/dmarcator.service" > /etc/systemd/system/dmarcator.service
    systemctl daemon-reload
}

# render_cron DB_NAME MAILTO DUMP_BINARY – /etc/cron.d/dmarcator from the template
render_cron() {
    sed -e "s|__USER__|$APP_USER|g" -e "s|__DIR__|$APP_DIR|g" -e "s|__DB__|$1|g" -e "s|__MAILTO__|$2|g" \
        -e "s|mariadb-dump |$3 |" -e "s|/var/backups/dmarcator|$BACKUP_DIR|g" \
        "$APP_DIR/deploy/cron/dmarcator" > /etc/cron.d/dmarcator
    chmod 644 /etc/cron.d/dmarcator
}

# ------------------------------------------------------------------ rename DMARCula → DMARCator (1.2.0)
# Installations made before 1.2.0 use the name "dmarcula" for the service, directory, system user,
# database, cron file and backups. Rename them once; the normal update below then takes over.
migrate_from_dmarcula() {
    local old_unit=/etc/systemd/system/dmarcula.service old_dir new_dir old_user url sql db_name=dmarcula
    [[ -f $old_unit && ! -f /etc/systemd/system/dmarcator.service ]] || return 0
    old_dir="$(sed -n 's/^WorkingDirectory=//p' "$old_unit" | head -1)"
    if [[ ! -d "$old_dir/.git" || ! -f "$old_dir/.env" ]]; then
        die "dmarcula.service points to '$old_dir', which is not an installation (moved by hand?). Restore that path, or remove dmarcula.service, then run the installer again."
    fi
    # nothing is changed unless the new code can be fetched
    git ls-remote --exit-code "$REPO_URL" "$BRANCH" >/dev/null 2>&1 \
        || die "Cannot reach $REPO_URL – nothing was changed. Check network access to GitHub and try again."
    step "Renaming the DMARCula installation in $old_dir to DMARCator"
    APP_PORT="$(sed -n 's/.*--port \([0-9]*\).*/\1/p' "$old_unit" | head -1)"
    APP_PORT="${APP_PORT:-8000}"
    local mailto="" dump="mariadb-dump"
    if [[ -f /etc/cron.d/dmarcula ]]; then
        mailto="$(sed -n 's/^MAILTO=//p' /etc/cron.d/dmarcula | head -1)"
        grep -q 'mysqldump ' /etc/cron.d/dmarcula && dump=mysqldump
    fi
    old_user="$(stat -c %U "$old_dir")"
    systemctl disable --now dmarcula >/dev/null 2>&1 || true

    # directory: …/dmarcula → …/dmarcator (the virtualenv has absolute paths, so it is rebuilt)
    new_dir="$old_dir"
    if [[ "$(basename "$old_dir")" == dmarcula && ! -e "$(dirname "$old_dir")/dmarcator" ]]; then
        new_dir="$(dirname "$old_dir")/dmarcator"
        mv "$old_dir" "$new_dir"
        rm -rf "$new_dir/.venv"
        info "Directory: $new_dir"
    fi
    APP_DIR="$new_dir"

    # system user (and its group) dmarcula → dmarcator
    APP_USER="$old_user"
    if [[ "$old_user" == dmarcula ]] && ! id dmarcator >/dev/null 2>&1; then
        usermod -l dmarcator dmarcula
        if getent group dmarcula >/dev/null; then groupmod -n dmarcator dmarcula; fi
        APP_USER=dmarcator
        info "System user: dmarcator"
    fi
    if [[ "$(getent passwd "$APP_USER" | cut -d: -f6)" == "$old_dir" ]]; then usermod -d "$new_dir" "$APP_USER"; fi

    # MariaDB database and account dmarcula → dmarcator (all tables move in one atomic statement)
    url="$(env_get DATABASE_URL)"
    sql="$(command -v mariadb || command -v mysql || true)"
    if [[ "$url" == mysql*://dmarcula:*@*/dmarcula && -n "$sql" ]] && "$sql" -e 'SELECT 1' >/dev/null 2>&1 \
            && [[ -z "$("$sql" -N -e "SHOW DATABASES LIKE 'dmarcator'")" ]]; then
        local t h renames=""
        for t in $("$sql" -N -e "SELECT table_name FROM information_schema.tables WHERE table_schema='dmarcula'"); do
            renames+="${renames:+, }\`dmarcula\`.\`$t\` TO \`dmarcator\`.\`$t\`"
        done
        "$sql" -e "CREATE DATABASE \`dmarcator\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        if [[ -z "$renames" ]] || "$sql" -e "SET FOREIGN_KEY_CHECKS=0; RENAME TABLE $renames"; then
            for h in localhost 127.0.0.1; do
                if [[ -n "$("$sql" -N -e "SELECT 1 FROM mysql.user WHERE user='dmarcula' AND host='$h'")" ]]; then
                    "$sql" -e "RENAME USER 'dmarcula'@'$h' TO 'dmarcator'@'$h'; GRANT ALL PRIVILEGES ON \`dmarcator\`.* TO 'dmarcator'@'$h'"
                fi
            done
            "$sql" -e "FLUSH PRIVILEGES; DROP DATABASE \`dmarcula\`"
            env_set DATABASE_URL "$(sed -e 's|://dmarcula:|://dmarcator:|' -e 's|/dmarcula$|/dmarcator|' <<<"$url")"
            if [[ -f "$APP_DIR/.backup.my.cnf" ]]; then sed -i 's/^user=dmarcula$/user=dmarcator/' "$APP_DIR/.backup.my.cnf"; fi
            db_name=dmarcator
            info "Database and account: dmarcator"
        else
            "$sql" -e "DROP DATABASE \`dmarcator\`"
            warn "Database tables could not be moved – the database stays 'dmarcula' (the application works with it)."
        fi
    elif [[ "$url" == mysql*/dmarcula ]]; then
        warn "MariaDB root access not available – the database stays 'dmarcula' (the application works with it)."
    fi
    if [[ "$(env_get APP_NAME)" == DMARCula ]]; then env_set APP_NAME DMARCator; fi

    # code from the renamed repository
    as_app git -C "$APP_DIR" remote set-url origin "$REPO_URL"
    as_app git -C "$APP_DIR" pull --ff-only
    cd "$APP_DIR"
    make_venv

    # backups: /var/backups/dmarcula → /var/backups/dmarcator
    if [[ -d /var/backups/dmarcula && ! -e "$BACKUP_DIR" ]]; then
        mv /var/backups/dmarcula "$BACKUP_DIR"
        local f
        for f in "$BACKUP_DIR"/dmarcula-*; do
            if [[ -e "$f" ]]; then mv "$f" "$BACKUP_DIR/dmarcator-${f##*/dmarcula-}"; fi
        done
        chown -R "$APP_USER:$APP_USER" "$BACKUP_DIR"
    fi
    if [[ -f /etc/cron.d/dmarcula ]]; then
        rm -f /etc/cron.d/dmarcula
        render_cron "$db_name" "$mailto" "$dump"
    fi

    # service dmarcula → dmarcator
    rm -f "$old_unit"
    render_unit
    systemctl enable dmarcator >/dev/null 2>&1
    info "Service: dmarcator (web server configuration is unchanged – it only points to 127.0.0.1:${APP_PORT})"
}

# ------------------------------------------------------------------ checks
[[ $EUID -eq 0 ]] || die "Run as root:  sudo bash install.sh"
[[ -d /run/systemd/system ]] || die "systemd is required. For other systems see docs/installation.md."
[[ -r /etc/os-release ]] || die "Cannot detect the distribution (/etc/os-release missing)."
# shellcheck disable=SC1091
. /etc/os-release
case " ${ID:-} ${ID_LIKE:-} " in
    *" debian "*|*" ubuntu "*)                     FAMILY=debian ;;
    *" rhel "*|*" fedora "*|*" centos "*)          FAMILY=rhel ;;
    *" suse "*|*" opensuse "*|*" sles "*)          FAMILY=suse ;;
    *) die "Unsupported distribution: ${PRETTY_NAME:-unknown}. Follow the manual steps in docs/installation.md." ;;
esac

PANEL=""
for p in /usr/local/hestia:HestiaCP /usr/local/vesta:VestaCP /usr/local/cpanel:cPanel /usr/local/directadmin:DirectAdmin \
         /usr/local/ispconfig:ISPConfig /etc/webmin/virtual-server:Virtualmin /usr/local/psa:Plesk /home/clp:CloudPanel; do
    [[ -e "${p%%:*}" ]] && { PANEL="${p##*:}"; break; }
done

printf '%sDMARCator installer%s – %s\n' "$B" "$N" "${PRETTY_NAME:-$ID}"
cd /    # the app user must be able to access the working directory
migrate_from_dmarcula

# ------------------------------------------------------------------ existing install → update
APP_USER="${APP_USER:-dmarcator}"
# /home/dmarc/dmarcator: layout used by early versions (as /home/dmarc/dmarcula, renamed above)
for candidate in "${APP_DIR:-}" /opt/dmarcator /home/dmarc/dmarcator "/home/$APP_USER/dmarcator"; do
    if [[ -n "$candidate" && -f "$candidate/.env" && -d "$candidate/.git" && -f /etc/systemd/system/dmarcator.service ]]; then
        APP_DIR="$candidate"
        APP_USER="$(stat -c %U "$APP_DIR")"
        step "Existing installation found in $APP_DIR – updating"
        cd "$APP_DIR"
        as_app git pull --ff-only
        as_app .venv/bin/pip install --quiet --upgrade -r requirements.txt
        systemctl restart dmarcator
        APP_PORT="$(sed -n 's/.*--port \([0-9]*\).*/\1/p' /etc/systemd/system/dmarcator.service | head -1)"
        APP_PORT="${APP_PORT:-8000}"
        info "Version: $(as_app .venv/bin/python -m app.manage version)"
        wait_healthy || die "Service did not come up – check: journalctl -u dmarcator -n 50"
        info "Updated and running."
        exit 0
    fi
done

# ------------------------------------------------------------------ questions
step "A few questions (press Enter for the default)"
ask DMARC_HOST "Host name of the web interface (DNS must point to this server), e.g. dmarc.example.com" "$(hostname -f 2>/dev/null || hostname)"
[[ -n "$DMARC_HOST" ]] || die "A host name is required."

ask APP_DIR "Install directory" "/opt/dmarcator"
[[ "$APP_DIR" == *public_html* || "$APP_DIR" == /var/www* ]] && die "Do not install into a web root ($APP_DIR) – .env and the database password would be downloadable."

if [[ -n "$PANEL" ]]; then
    warn "Hosting panel detected: $PANEL. The web server will NOT be changed – you'll get the proxy lines to add in the panel."
    WEB="${WEB:-none}"
fi
if [[ -z "${WEB:-}" ]]; then
    if command -v apache2ctl >/dev/null 2>&1 || command -v httpd >/dev/null 2>&1; then existing=apache
    elif command -v nginx >/dev/null 2>&1; then existing=nginx
    else existing=apache; fi
    ask WEB "Web server: apache, nginx or none" "$existing"
fi
[[ "$WEB" =~ ^(apache|nginx|none)$ ]] || die "WEB must be apache, nginx or none."

if [[ "$WEB" != none ]]; then
    ask LETSENCRYPT "Get a free Let's Encrypt HTTPS certificate? (needs public DNS + port 80 open) yes/no" "yes"
    [[ "$LETSENCRYPT" == yes ]] && ask LE_EMAIL "E-mail for Let's Encrypt expiry notices (optional)" ""
else
    LETSENCRYPT=no
fi

while :; do
    ask_secret ADMIN_PASSWORD "Password for the web 'admin' account (min. 10 chars, Enter = generate)"
    if [[ -z "$ADMIN_PASSWORD" ]]; then ADMIN_PASSWORD="$(openssl rand -base64 18 | tr -d '/+=')"; GENERATED_PW=1; break; fi
    if (( ${#ADMIN_PASSWORD} < 10 )) || [[ "$ADMIN_PASSWORD" == *"'"* || "$ADMIN_PASSWORD" == *\\* ]]; then
        warn "At least 10 characters, without ' or \\ ."; ADMIN_PASSWORD=""; tty_ok || die "Invalid ADMIN_PASSWORD."
    else break; fi
done
ask ALERT_EMAIL "E-mail for alerts from cron (backup/mailbox failures; optional, needs a local MTA)" ""

APP_PORT="${APP_PORT:-8000}"
while ! port_free "$APP_PORT"; do APP_PORT=$((APP_PORT + 1)); done

# ------------------------------------------------------------------ packages
step "Installing packages ($FAMILY)"
case "$FAMILY" in
    debian)
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq
        pkgs=(python3 python3-venv git curl ca-certificates openssl cron)
        command -v mariadbd >/dev/null 2>&1 || command -v mysqld >/dev/null 2>&1 || pkgs+=(mariadb-server mariadb-client)
        [[ "$WEB" == apache ]] && pkgs+=(apache2)
        [[ "$WEB" == nginx ]] && pkgs+=(nginx)
        [[ "$LETSENCRYPT" == yes ]] && pkgs+=(certbot "python3-certbot-$WEB")
        apt-get install -y -qq "${pkgs[@]}" >/dev/null
        CRON_SVC=cron ;;
    rhel)
        pkgs=(git curl openssl cronie python3)
        command -v mariadbd >/dev/null 2>&1 || command -v mysqld >/dev/null 2>&1 || pkgs+=(mariadb-server mariadb)
        [[ "$WEB" == apache ]] && pkgs+=(httpd mod_ssl)
        [[ "$WEB" == nginx ]] && pkgs+=(nginx)
        dnf install -y -q "${pkgs[@]}"
        dnf install -y -q python3.12 >/dev/null 2>&1 || dnf install -y -q python3.11 >/dev/null 2>&1 || true
        if [[ "$LETSENCRYPT" == yes ]]; then
            if [[ "${ID}" != fedora ]] && ! rpm -q epel-release >/dev/null 2>&1; then
                dnf install -y -q epel-release 2>/dev/null \
                  || dnf install -y -q "https://dl.fedoraproject.org/pub/epel/epel-release-latest-$(rpm -E %rhel).noarch.rpm" \
                  || warn "EPEL not available – Let's Encrypt skipped."
            fi
            dnf install -y -q certbot "python3-certbot-$WEB" || { warn "certbot not available – Let's Encrypt skipped."; LETSENCRYPT=no; }
        fi
        CRON_SVC=crond ;;
    suse)
        pkgs=(git curl openssl cron python311)
        command -v mariadbd >/dev/null 2>&1 || command -v mysqld >/dev/null 2>&1 || pkgs+=(mariadb mariadb-client)
        [[ "$WEB" == apache ]] && pkgs+=(apache2)
        [[ "$WEB" == nginx ]] && pkgs+=(nginx)
        zypper --non-interactive --quiet install "${pkgs[@]}"
        if [[ "$LETSENCRYPT" == yes ]]; then
            zypper --non-interactive --quiet install certbot "python3-certbot-$WEB" || { warn "certbot not available – Let's Encrypt skipped."; LETSENCRYPT=no; }
        fi
        CRON_SVC=cron ;;
esac
systemctl enable --now "$CRON_SVC" >/dev/null 2>&1 || true

# ------------------------------------------------------------------ database
step "MariaDB database"
systemctl enable --now mariadb >/dev/null 2>&1 || systemctl enable --now mysql >/dev/null 2>&1 || systemctl enable --now mysqld >/dev/null 2>&1 \
    || die "Could not start MariaDB."
SQL_CLIENT="$(command -v mariadb || command -v mysql)" || die "MariaDB client not found."
DUMP_CMD="$(command -v mariadb-dump || command -v mysqldump)" || die "mariadb-dump not found."
if ! "$SQL_CLIENT" -e 'SELECT 1' >/dev/null 2>&1; then
    ask_secret DB_ROOT_PASSWORD "MariaDB root password (root login via socket did not work)"
    export MYSQL_PWD="$DB_ROOT_PASSWORD"
    SQL_CLIENT_ARGS=(-u root)
    "$SQL_CLIENT" "${SQL_CLIENT_ARGS[@]}" -e 'SELECT 1' >/dev/null 2>&1 || die "Cannot log in to MariaDB as root."
else
    SQL_CLIENT_ARGS=()
fi
DB_PASS="$(openssl rand -hex 24)"
"$SQL_CLIENT" "${SQL_CLIENT_ARGS[@]}" <<SQL
CREATE DATABASE IF NOT EXISTS \`${DB_NAME}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASS}';
CREATE USER IF NOT EXISTS '${DB_USER}'@'127.0.0.1' IDENTIFIED BY '${DB_PASS}';
ALTER USER '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASS}';
ALTER USER '${DB_USER}'@'127.0.0.1' IDENTIFIED BY '${DB_PASS}';
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${DB_USER}'@'localhost';
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${DB_USER}'@'127.0.0.1';
FLUSH PRIVILEGES;
SQL
unset MYSQL_PWD
info "Database '${DB_NAME}' and user '${DB_USER}' ready."

# ------------------------------------------------------------------ user, code, python
step "Application user and code"
# dedicated system user without login shell; its home is the application directory
id "$APP_USER" >/dev/null 2>&1 || useradd --system --no-create-home --home-dir "$APP_DIR" \
    --shell "$(command -v nologin || echo /usr/sbin/nologin)" "$APP_USER"
info "System user: $APP_USER"
install -d -o "$APP_USER" -g "$APP_USER" -m 750 "$APP_DIR"
if [[ -d "$APP_DIR/.git" ]]; then
    as_app git -C "$APP_DIR" pull --ff-only
else
    as_app git clone --quiet --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"

step "Python environment"
make_venv

# ------------------------------------------------------------------ configuration
step "Configuration (.env)"
as_app cp .env.example .env
chmod 600 .env
env_set SECRET_KEY "$(openssl rand -hex 32)"
env_set BASE_URL "http://${DMARC_HOST}"
env_set COOKIE_SECURE false
env_set DATABASE_URL "mysql+pymysql://${DB_USER}:${DB_PASS}@127.0.0.1:3306/${DB_NAME}"
env_set ADMIN_USERNAME admin
env_set ADMIN_PASSWORD "$ADMIN_PASSWORD"
chown "$APP_USER:$APP_USER" .env
as_app .venv/bin/python -c "from app.db import init_db; init_db()" || die "Database initialisation failed – see the error above."
info "Database schema created."

# credentials for the backup cron job
printf '[client]\nuser=%s\npassword=%s\nhost=127.0.0.1\n' "$DB_USER" "$DB_PASS" > "$APP_DIR/.backup.my.cnf"
chown "$APP_USER:$APP_USER" "$APP_DIR/.backup.my.cnf"; chmod 600 "$APP_DIR/.backup.my.cnf"

# ------------------------------------------------------------------ systemd service
step "systemd service 'dmarcator'"
render_unit
systemctl enable --now dmarcator >/dev/null 2>&1
systemctl restart dmarcator
wait_healthy || die "The service did not start – check: journalctl -u dmarcator -n 50"
info "Running on 127.0.0.1:${APP_PORT}."
env_set ADMIN_PASSWORD ""      # only needed for the first start; the account now exists in the database

# ------------------------------------------------------------------ web server
selinux_proxy() {
    if command -v selinuxenabled >/dev/null 2>&1 && selinuxenabled; then setsebool -P httpd_can_network_connect 1; fi
}
render() { sed -e "s|__HOST__|$DMARC_HOST|g" -e "s|__PORT__|$APP_PORT|g" "deploy/$1/dmarcator.conf"; }

if [[ "$WEB" != none ]] && grep -rqsE "^\s*(ServerName|ServerAlias|server_name)\s.*\b${DMARC_HOST//./\\.}\b" \
        /etc/apache2 /etc/httpd /etc/nginx 2>/dev/null \
        && ! grep -rqsl "127.0.0.1:${APP_PORT}" /etc/apache2 /etc/httpd /etc/nginx 2>/dev/null; then
    warn "A site for ${DMARC_HOST} already exists in the web server configuration – it will not be changed."
    WEB=existing
fi

case "$WEB:$FAMILY" in
    apache:debian)
        step "Apache"
        a2enmod -q proxy proxy_http headers ssl >/dev/null
        render apache > /etc/apache2/sites-available/dmarcator.conf
        a2ensite -q dmarcator >/dev/null
        systemctl enable --now apache2 >/dev/null 2>&1
        apache2ctl configtest || die "Apache configuration test failed."
        systemctl restart apache2 ;;
    apache:rhel)
        step "Apache"
        render apache > /etc/httpd/conf.d/dmarcator.conf
        selinux_proxy
        systemctl enable --now httpd >/dev/null 2>&1
        apachectl configtest || die "Apache configuration test failed."
        systemctl reload httpd ;;
    apache:suse)
        step "Apache"
        for m in proxy proxy_http headers ssl; do a2enmod "$m" >/dev/null 2>&1 || true; done
        render apache > /etc/apache2/vhosts.d/dmarcator.conf
        systemctl enable --now apache2 >/dev/null 2>&1
        apachectl configtest || die "Apache configuration test failed."
        systemctl restart apache2 ;;
    nginx:debian)
        step "nginx"
        render nginx > /etc/nginx/sites-available/dmarcator
        ln -sf /etc/nginx/sites-available/dmarcator /etc/nginx/sites-enabled/dmarcator
        systemctl enable --now nginx >/dev/null 2>&1
        nginx -t || die "nginx configuration test failed."
        systemctl reload nginx ;;
    nginx:rhel)
        step "nginx"
        render nginx > /etc/nginx/conf.d/dmarcator.conf
        selinux_proxy
        systemctl enable --now nginx >/dev/null 2>&1
        nginx -t || die "nginx configuration test failed."
        systemctl reload nginx ;;
    nginx:suse)
        step "nginx"
        install -d /etc/nginx/vhosts.d
        render nginx > /etc/nginx/vhosts.d/dmarcator.conf
        systemctl enable --now nginx >/dev/null 2>&1
        nginx -t || die "nginx configuration test failed."
        systemctl reload nginx ;;
esac

if [[ "$WEB" == apache || "$WEB" == nginx ]]; then
    if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
        ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
    elif systemctl is-active --quiet firewalld; then
        firewall-cmd --quiet --permanent --add-service=http --add-service=https && firewall-cmd --quiet --reload
    fi
fi

# ------------------------------------------------------------------ HTTPS
SCHEME=http
if [[ "$LETSENCRYPT" == yes && ( "$WEB" == apache || "$WEB" == nginx ) ]]; then
    step "Let's Encrypt certificate"
    email_args=(--register-unsafely-without-email)
    [[ -n "${LE_EMAIL:-}" ]] && email_args=(-m "$LE_EMAIL")
    if certbot "--$WEB" -d "$DMARC_HOST" --redirect --agree-tos --non-interactive "${email_args[@]}"; then
        SCHEME=https
        env_set BASE_URL "https://${DMARC_HOST}"
        env_set COOKIE_SECURE true
        systemctl restart dmarcator
        wait_healthy || warn "Service restart after enabling HTTPS took too long – check journalctl -u dmarcator."
    else
        warn "Let's Encrypt failed (is ${DMARC_HOST} public and port 80 reachable?). The site works over HTTP for now."
        warn "For an internal host name use your own certificate – see docs/installation.md (Apache / nginx sections)."
    fi
fi

# ------------------------------------------------------------------ cron
step "Scheduled jobs (/etc/cron.d/dmarcator)"
install -d -o "$APP_USER" -g "$APP_USER" -m 750 "$BACKUP_DIR"
render_cron "$DB_NAME" "${ALERT_EMAIL:-}" "$(basename "$DUMP_CMD")"
info "Backups go to ${BACKUP_DIR}."

# ------------------------------------------------------------------ done
step "Done"
URL="${SCHEME}://${DMARC_HOST}"
if [[ "$WEB" == none || "$WEB" == existing ]]; then
    cat <<EOF
    The application runs on http://127.0.0.1:${APP_PORT}, but the web server was not changed.
    Point the site ${DMARC_HOST} at it – add these lines to its virtual host
    (in a hosting panel: "custom Apache directives" / "proxy" settings of the site):

      Apache:
        ProxyPreserveHost On
        ProxyPass        / http://127.0.0.1:${APP_PORT}/ timeout=120
        ProxyPassReverse / http://127.0.0.1:${APP_PORT}/
        RequestHeader set X-Forwarded-Proto "https" env=HTTPS

      nginx (inside server { }):
        location / { proxy_pass http://127.0.0.1:${APP_PORT}; proxy_set_header Host \$host;
                     proxy_set_header X-Forwarded-Proto \$scheme; client_max_body_size 60m; }

    With HTTPS, set BASE_URL=https://${DMARC_HOST} and COOKIE_SECURE=true in ${APP_DIR}/.env
    and run: systemctl restart dmarcator
EOF
fi
cat <<EOF

    Open:       ${B}${URL}${N}
    Login:      admin
    Password:   ${GENERATED_PW:+${B}}${ADMIN_PASSWORD}${N}${GENERATED_PW:+   (generated – write it down)}

    Next steps:
      1. In the web UI: Administrácia → Poštové schránky → add the Exchange Online mailbox
         (guide: docs/microsoft-365.md)
      2. Optional LDAP / SSO login: Administrácia → Prihlásenie (LDAP / SSO)
         (guide: docs/authentication.md)

    Useful commands:
      systemctl status dmarcator              service state
      journalctl -u dmarcator -f              application log
      sudo bash ${APP_DIR}/deploy/install.sh update to the latest version
EOF
