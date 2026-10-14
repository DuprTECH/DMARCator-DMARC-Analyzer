# Configuration reference

DMARCula reads its settings from environment variables or from a `.env` file in the working
directory. Start from [`.env.example`](../.env.example). Variable names are case-insensitive
and booleans are `true` / `false`.

Mailboxes are **not** configured in `.env`. Add them in the UI (*Administrácia → Poštové
schránky*). Their credentials are stored encrypted in the database.

## Basics

| Variable | Default | Description |
|---|---|---|
| `APP_NAME` | `DMARCula` | Name shown in the UI and page titles. |
| `DEFAULT_LANGUAGE` | `en` | The UI language follows the browser: Slovak if the browser's preferred language is Slovak, English otherwise. This value is used only when the browser sends no language. A choice made with SK / EN in the top bar is stored in a cookie and takes precedence. |
| `BASE_URL` | `http://localhost:8000` | Public URL of the app. Used to build the SSO redirect URI. |
| `SECRET_KEY` | – (**required**) | Signs session cookies. The encryption key for stored mailbox credentials is derived from it. Minimum 16 characters, 48+ random characters recommended. Changing it logs everyone out, and stored mailbox credentials must be re-entered. |
| `DATABASE_URL` | `sqlite:///./data/dmarcula.db` | SQLAlchemy URL, see below. |
| `SESSION_MAX_AGE_HOURS` | `12` | Absolute session lifetime. |
| `COOKIE_SECURE` | `false` | Set `true` when served over HTTPS. |

### Database URLs

| Database | `DATABASE_URL` |
|---|---|
| MariaDB / MySQL (recommended for many domains) | `mysql+pymysql://dmarcula:PASSWORD@localhost:3306/dmarcula` |
| PostgreSQL | `postgresql+psycopg://dmarcula:PASSWORD@localhost:5432/dmarcula` |
| SQLite (single file, zero setup) | `sqlite:///./data/dmarcula.db` (relative) or `sqlite:////var/lib/dmarcula/dmarcula.db` (absolute) |

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

| Variable | Default | Description |
|---|---|---|
| `SCHEDULER_ENABLED` | `true` | Built-in scheduler for mailbox polling and DNS refresh. Set `false` to drive both from cron with `python -m app.manage poll` / `dns` instead (see [Operations](operations.md#cron-jobs)). Never run both. |
| `POLL_INTERVAL_MINUTES` | `15` | How often all enabled mailboxes are polled by the built-in scheduler. |
| `DNS_REFRESH_HOURS` | `24` | How often every domain's DNS posture is re-checked (8 domains in parallel). New domains are checked right after their first report. |
| `RESOLVE_PTR` | `true` | Reverse DNS for source IPs. Needed to recognise sending services. |
| `DNS_SERVERS` | – (`.env.example`: `1.1.1.1,8.8.8.8`) | Comma-separated resolvers for DNS checks and PTR lookups. Empty means the system resolvers. **Use public resolvers** when the server's own DNS has an internal copy of your zones (split-horizon / Active Directory DNS). Such a server answers "no TXT record" and every domain looks like it has no SPF, DKIM or DMARC. The app flags this when reports prove that a record exists. A failed lookup (timeout) is shown as *Nezistené*, never as "missing". |
| `GEOIP_COUNTRY_DB` | – | Path to MaxMind `GeoLite2-Country.mmdb`. |
| `GEOIP_ASN_DB` | – | Path to MaxMind `GeoLite2-ASN.mmdb`. |
| `MAX_ATTACHMENT_MB` | `50` | Maximum size of one message or uploaded file. Extracted archives have a fixed 200 MB limit. |

GeoLite2 databases are free after registering at maxmind.com. Download them and point the two
variables at the `.mmdb` files. Without them, the country map and ASN columns stay empty.

## Mailbox settings (in the UI)

| Field | Graph | IMAP | Description |
|---|:-:|:-:|---|
| Tenant ID, Client ID | ✔ | OAuth2 | From the Entra ID app registration. |
| Client secret / certificate (PEM + SHA-1 thumbprint) | ✔ | secret only | App credential. |
| Mailbox | ✔ | – | UPN or SMTP address (a shared mailbox works). |
| Host, port, security, username, password | – | ✔ | IMAP connection. |
| Report folder | ✔ | ✔ | `Inbox`, `Inbox/DMARC`, … (IMAP: `INBOX`). |
| Move processed to | ✔ | ✔ | Created if missing. Empty means messages aren't moved. |
| Move failed to | ✔ | ✔ | Messages without a valid report. |
| Only unread / mark as read | ✔ | ✔ | With "only unread" off, the whole folder is processed. Duplicates are skipped. |
| Authority host, Graph endpoint | ✔ | – | National clouds (GCC High, 21Vianet). |

At most 500 messages are processed per mailbox per run. A backlog is worked off over
consecutive runs.
