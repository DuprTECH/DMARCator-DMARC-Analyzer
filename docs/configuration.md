# Configuration reference

DMARCator reads its settings from environment variables or from a `.env` file in the working
directory. Start from [`.env.example`](../.env.example). Variable names are case-insensitive
and booleans are `true` / `false`.

Mailboxes are **not** configured in `.env`. Add them in the UI (*Administrácia → Poštové
schránky*). Their credentials are stored encrypted in the database.

## Basics

| Variable | Default | Description |
|---|---|---|
| `APP_NAME` | `DMARCator` | Name shown in the UI and page titles. |
| `DEFAULT_LANGUAGE` | `en` | The UI language follows the browser: Slovak if the browser's preferred language is Slovak, English otherwise. This value is used only when the browser sends no language. A choice made with SK / EN in the top bar is stored in a cookie and takes precedence. |
| `BASE_URL` | `http://localhost:8000` | Public URL of the app. Used to build the SSO redirect URI. |
| `SECRET_KEY` | – (**required**) | Signs session cookies. The encryption key for stored mailbox credentials is derived from it. Minimum 16 characters, 48+ random characters recommended. Changing it logs everyone out, and stored mailbox credentials must be re-entered. |
| `DATABASE_URL` | `sqlite:///./data/dmarcator.db` | SQLAlchemy URL, see below. |
| `SESSION_MAX_AGE_HOURS` | `12` | Absolute session lifetime. |
| `COOKIE_SECURE` | `false` | Set `true` when served over HTTPS. |

### Database URLs

| Database | `DATABASE_URL` |
|---|---|
| MariaDB / MySQL (recommended for many domains) | `mysql+pymysql://dmarcator:PASSWORD@localhost:3306/dmarcator` |
| PostgreSQL | `postgresql+psycopg://dmarcator:PASSWORD@localhost:5432/dmarcator` |
| SQLite (single file, zero setup) | `sqlite:///./data/dmarcator.db` (relative) or `sqlite:////var/lib/dmarcator/dmarcator.db` (absolute) |

Notes:
- **MariaDB:** create the database with `CHARACTER SET utf8mb4`. The app adds
  `charset=utf8mb4` to the connection automatically and recycles connections every 30 minutes,
  before `wait_timeout` closes them.
- **Special characters** in passwords must be URL-encoded (`@` → `%40`, `/` → `%2F`, `:` → `%3A`).
- Tables, new columns and new indexes are created automatically at startup.

## Local accounts

| Variable | Default | Description |
|---|---|---|
| `ADMIN_USERNAME` | `admin` | Username of the first administrator. |
| `ADMIN_PASSWORD` | – | Password of the first administrator. Used only when the user table is empty. |
| `LOCAL_LOGIN_ENABLED` | `true` | `false` disables password login for local accounts (e.g. SSO only). This also disables the bootstrap admin. |
| `DEFAULT_ROLE` | `viewer` | Role of new LDAP/SSO users who are not in an admin group. |

## LDAP / Active Directory

The LDAP and SSO settings below can also be set in the web UI (*Administrácia → Prihlásenie (LDAP / SSO)*); values saved there override `.env`. See [Authentication](authentication.md#where-to-configure).


| Variable | Default | Description |
|---|---|---|
| `LDAP_ENABLED` | `false` | Enables LDAP login. |
| `LDAP_SERVER` | – | `ldaps://dc01.example.local:636`, or `ldap://…:389` combined with StartTLS. |
| `LDAP_START_TLS` | `false` | StartTLS on port 389. |
| `LDAP_TLS_VERIFY` | `true` | Verify the server certificate. |
| `LDAP_CA_FILE` | – | PEM file with your internal CA. |
| `LDAP_BIND_DN` | – | Service account DN used to search for users. Empty means the app binds directly as the user. |
| `LDAP_BIND_PASSWORD` | – | Service account password. |
| `LDAP_UPN_SUFFIX` | – | Appended to bare usernames when binding without a service account (`john` → `john@example.local`). |
| `LDAP_BASE_DN` | – | Search base, e.g. `DC=example,DC=local`. |
| `LDAP_USER_FILTER` | `(&(objectClass=user)(\|(sAMAccountName={username})(userPrincipalName={username})))` | `{username}` is replaced with the escaped login name. |
| `LDAP_ALLOWED_GROUP` | – | DN of the group allowed to log in. Empty means any valid account. |
| `LDAP_ADMIN_GROUP` | – | DN of the admin group. When set, the role is re-evaluated at every login. |
| `LDAP_NESTED_GROUPS` | `true` | Resolve nested groups (AD `LDAP_MATCHING_RULE_IN_CHAIN`). |
| `LDAP_ATTR_DISPLAY_NAME` | `displayName` | Attribute holding the full name. |
| `LDAP_ATTR_MAIL` | `mail` | Attribute holding the e-mail address. |

## SSO (OpenID Connect)

| Variable | Default | Description |
|---|---|---|
| `OIDC_ENABLED` | `false` | Shows the SSO button on the login page. |
| `OIDC_DISCOVERY_URL` | – | `.well-known/openid-configuration` URL. Entra ID: `https://login.microsoftonline.com/<TENANT_ID>/v2.0/.well-known/openid-configuration` |
| `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` | – | Client credentials. |
| `OIDC_SCOPES` | `openid profile email` | Requested scopes. |
| `OIDC_BUTTON_LABEL` | `Prihlásiť cez Microsoft` | Button text. |
| `OIDC_USERNAME_CLAIM` | `preferred_username` | Claim used as the username. Falls back to `upn`, `email`, `sub`. |
| `OIDC_GROUPS_CLAIM` | `groups` | Claim holding group IDs. The `roles` claim (Entra app roles) is always read as well. |
| `OIDC_ALLOWED_GROUPS` | – | Comma-separated group IDs or role names allowed to log in. Empty means anyone the IdP authenticates. |
| `OIDC_ADMIN_GROUPS` | – | Comma-separated group IDs or role names that get the admin role. |

## Processing

### Web display settings

Administrators can open **Administration → Time and language** to select a shared IANA time zone
(for Slovakia, `Europe/Bratislava`) and default language (`sk`, `en`, or browser detection).
The settings are stored in the database and apply on the next request, without restarting.
The top-bar SK/EN switch overrides the shared language for that browser; **AUTO** clears it.

`DISPLAY_TIMEZONE` defaults to `UTC`, and `DISPLAY_LANGUAGE` to `auto` when no web override exists.
In auto mode, browser language detection uses `DEFAULT_LANGUAGE` only when the browser supplies
no language. The time-zone conversion handles summer/winter time and applies to web timestamps,
including ingest logs, last runs and report details. Date-only report periods and chart day
boundaries are not shifted. Database timestamps remain UTC; CLI/server log formatting is unchanged.
Resetting sign-in settings does not reset display preferences.

| Variable | Default | Description |
|---|---|---|
| `SCHEDULER_ENABLED` | `true` | Built-in scheduler for mailbox polling and DNS refresh. Set `false` to drive both from cron with `python -m app.manage poll` / `dns` instead (see [Operations](operations.md#cron-jobs)). Never run both. |
| `POLL_INTERVAL_MINUTES` | `15` | How often all enabled mailboxes are polled by the built-in scheduler. |
| `DNS_REFRESH_HOURS` | `24` | How often every domain's DNS posture is re-checked (8 domains in parallel). New domains are checked right after their first report. |
| `RESOLVE_PTR` | `true` | Reverse DNS for source IPs. Needed to recognise sending services. |
| `DNS_SERVERS` | – (`.env.example`: `1.1.1.1,8.8.8.8`) | Comma-separated resolvers for DNS checks and PTR lookups. Empty means the system resolvers. **Use public resolvers** when the server's own DNS has an internal copy of your zones (split-horizon / Active Directory DNS). Such a server answers "no TXT record" and every domain looks like it has no SPF, DKIM or DMARC. The app flags this when reports prove that a record exists. A failed lookup (timeout) is shown as *Nezistené*, never as "missing". |
| `GEOIP_COUNTRY_DB` | – | Path to MaxMind `GeoLite2-Country.mmdb`. |
| `GEOIP_ASN_DB` | – | Path to MaxMind `GeoLite2-ASN.mmdb`. |
| `RAW_XML_MAX_KB` | `2048` | Aggregate reports larger than this are stored without their original XML (all records are still stored). Keeps inserts below MariaDB's `max_allowed_packet`. |
| `MAX_ATTACHMENT_MB` | `50` | Maximum size of one message or uploaded file. Extracted archives have a fixed 200 MB limit. |

GeoLite2 databases are free after registering at maxmind.com. Download them and point the two
variables at the `.mmdb` files. Without them, the country map and ASN columns stay empty.

### Setting up GeoLite2 (countries map and ASNs)

1. **Account:** free sign-up at <https://www.maxmind.com/en/geolite2/signup>, then
   *Manage License Keys → Generate new license key*. You need the numeric **Account ID** and the key.
2. **Download** (Debian: `geoipupdate` is in the `contrib` component; RHEL-like: EPEL):

   ```bash
   sudo apt install geoipupdate
   sudo tee /etc/GeoIP.conf >/dev/null <<'CONF'
   AccountID 123456
   LicenseKey YOUR_LICENSE_KEY
   EditionIDs GeoLite2-Country GeoLite2-ASN
   DatabaseDirectory /var/lib/GeoIP
   CONF
   sudo chmod 640 /etc/GeoIP.conf
   sudo mkdir -p /var/lib/GeoIP && sudo geoipupdate -v && ls -l /var/lib/GeoIP/*.mmdb
   ```

   The Debian package enables `geoipupdate.timer` for automatic updates; elsewhere add a weekly
   cron job running `geoipupdate`.
3. **Configure** the paths in `.env` and restart the service:

   ```
   GEOIP_COUNTRY_DB=/var/lib/GeoIP/GeoLite2-Country.mmdb
   GEOIP_ASN_DB=/var/lib/GeoIP/GeoLite2-ASN.mmdb
   ```

4. **Backfill** reports imported before (country and ASN are stored at import time):
   `python -m app.manage geoip-refresh`, then restart the service.

| Problem | Fix |
|---|---|
| `geoipupdate`: HTTP **401** – account ID or license key could not be authenticated | Account ID must be the number, not the e-mail; a new key needs a few minutes to activate; the key is shown only once – generate a new one if unsure. |
| `GeoIP DB … unavailable: No such file or directory` | The download hasn't succeeded yet – run `geoipupdate -v`. |
| Map stays empty although countries are listed | The browser loads the world map from `cdn.jsdelivr.net`; allow it. |

## Mailbox settings (in the UI)

| Field | Graph | IMAP | Description |
|---|:-:|:-:|---|
| Tenant ID, Client ID | ✔ | OAuth2 | From the Entra ID app registration. |
| Client secret / certificate (PEM + SHA-1 thumbprint) | ✔ | secret only | App credential. |
| Mailbox | ✔ | – | UPN or SMTP address (a shared mailbox works). |
| Host, port, security, username, password | – | ✔ | IMAP connection. |
| Report folder | ✔ | ✔ | `Inbox`, `Inbox/DMARC`, … (IMAP: `INBOX`). |
| Move processed to | ✔ | ✔ | Created if missing. Empty means messages aren't moved. |
| Move failed to | ✔ | ✔ | Messages whose report could not be read or stored (logged as *error*). |
| Move messages without a report to | ✔ | ✔ | Ordinary mail, forwards, auto-replies and forensic reports that identify nothing (logged as *info*). Empty means the same folder as failed messages. |
| Only unread / mark as read | ✔ | ✔ | With "only unread" off, the whole folder is processed. Duplicates are skipped. |
| Automatically delete old processed messages / retention days | ✔ | – | Off by default; 30 days suggested, allowed range 1–3650 days. Global Microsoft Graph only. Requires a dedicated processed folder separate from input and failed folders. |
| Authority host, Graph endpoint | ✔ | – | National clouds (GCC High, 21Vianet). |

At most 500 messages are processed per mailbox per run. A backlog is worked off over
consecutive runs.

### Optional mailbox message retention

Enabling retention authorizes irreversible `permanentDelete` calls for up to 500 old messages
after each successful polling pass. Age is measured from the message's **received date**, not its
processing date; an old backlog report can therefore be deleted immediately after successful import.
Only messages explicitly marked by DMARCator as successfully processed **while retention is enabled**
are eligible, and only within the configured processed folder (not its subfolders).
Existing unmarked mail, failed reports and ordinary notifications are not deleted. A report must
have been recognized and all its reports saved or identified as duplicates before it is marked.

This is age-based retention, **not quota monitoring**: it cannot guarantee 10% free space.
Exchange may retain permanently deleted messages in Recoverable Items under its retention/hold
policies. Stored database reports are unaffected. Verify backups and organizational retention
requirements before enabling. Errors are recorded in the source log and abort that polling run;
already completed deletions cannot be rolled back. Each deletion is recorded in the server log.
