# Authentication and roles

Three login methods are available, and they can be enabled at the same time:

| Method | How accounts are created | Role |
|---|---|---|
| **Local account** | manually in *Administrácia → Používatelia* (the first admin comes from `.env`) | set by an admin |
| **LDAP / AD** | automatically at first login | from `LDAP_ADMIN_GROUP`, otherwise `DEFAULT_ROLE` |
| **SSO (OIDC)** | automatically at first login | from `OIDC_ADMIN_GROUPS`, otherwise `DEFAULT_ROLE` |

## Where to configure

Open **Administrácia → Prihlásenie (LDAP / SSO)** in the web UI (admins only). It configures local
accounts, LDAP / Active Directory and SSO (OpenID Connect). Changes apply immediately, without a restart.

- **Test before saving:** *Otestovať prihlásenie* signs a real user in against LDAP with the values
  currently in the form and shows each step: connection, service account, user search, password,
  group membership and resulting role. *Overiť nastavenie SSO* loads the OIDC discovery document and
  shows the redirect URI to register in Entra ID.
- Passwords and client secrets are stored encrypted (key derived from `SECRET_KEY`) and are never
  shown again; leave the field empty to keep the stored value.
- Values saved in the UI override the `LDAP_*` / `OIDC_*` variables in `.env`. *Vrátiť na hodnoty z .env*
  drops them again.
- **Lock-out protection:** local sign-in can only be turned off while you are signed in via LDAP or SSO,
  which proves that it works. Emergency rollback on the server:
  `cd /opt/dmarcator && runuser -u dmarcator -- .venv/bin/python -m app.manage auth-reset && systemctl restart dmarcator`.

The sections below explain the individual settings; the same names are used in the UI and in `.env`.

## Roles

| Role | Permissions |
|---|---|
| `viewer` | All dashboards, domains, reports, tools, CSV export, and re-running a domain's DNS check. |
| `admin` | Everything a viewer can do, plus managing mailboxes, importing reports, managing users, and deleting domains with their data. |

**Group mapping.** When an admin group is configured (`LDAP_ADMIN_GROUP` or
`OIDC_ADMIN_GROUPS`), the role of an external account is **recalculated at every login**, so a
manual change in the UI is overwritten at the next login. When no admin group is configured,
the role is set once when the account is created and is managed manually after that.

**Username collisions.** A username belongs to exactly one source. If a local account `john`
exists, LDAP login as `john` checks the local password instead. If an SSO account `john@example.com`
exists, an LDAP login with the same name is refused. This prevents taking over an account
through a different identity source.

**Deactivation.** A disabled account (*aktívny* unchecked) cannot log in by any method. For
LDAP and SSO users, it's more robust to remove them from the allowed group.

## Local accounts

- Passwords must be at least 10 characters long and are stored as salted `scrypt` hashes.
- After 8 failed attempts for the same IP + username, logins are blocked for 5 minutes.
- To use SSO only, create an SSO admin first, then set `LOCAL_LOGIN_ENABLED=false`.

## LDAP / Active Directory

### Option A: with a service account (recommended)

```ini
LDAP_ENABLED=true
LDAP_SERVER=ldaps://dc01.example.local:636
LDAP_BIND_DN=CN=svc-dmarcator,OU=Service Accounts,DC=example,DC=local
LDAP_BIND_PASSWORD=********
LDAP_BASE_DN=DC=example,DC=local
LDAP_ALLOWED_GROUP=CN=DMARC-Users,OU=Groups,DC=example,DC=local
LDAP_ADMIN_GROUP=CN=DMARC-Admins,OU=Groups,DC=example,DC=local
```

The login flow:
1. Bind with the service account and find **exactly one** user with `LDAP_USER_FILTER`.
2. Verify the password with a second bind as that user.
3. Check membership of `LDAP_ALLOWED_GROUP` and `LDAP_ADMIN_GROUP`, including nested groups.

The service account can be a plain domain user without extra rights.

### Option B: without a service account

```ini
LDAP_ENABLED=true
LDAP_SERVER=ldaps://dc01.example.local:636
LDAP_UPN_SUFFIX=example.local
LDAP_BASE_DN=DC=example,DC=local
```

The user binds directly as `name@example.local`. Users may type `name`, `EXAMPLE\name` or the
full UPN. The user's own connection is then used to read attributes and groups.

### Certificates

LDAPS verifies the domain controller's certificate. If it was issued by an internal CA, export
the root certificate as PEM and set `LDAP_CA_FILE=/path/ca.pem`. Use `LDAP_TLS_VERIFY=false`
for testing only.

Quick connectivity test from the server:

```bash
openssl s_client -connect dc01.example.local:636 -showcerts </dev/null | head -20
```

### Other LDAP servers (OpenLDAP, FreeIPA)

Adjust the filter and attributes:

```ini
LDAP_USER_FILTER=(&(objectClass=inetOrgPerson)(uid={username}))
LDAP_NESTED_GROUPS=false
LDAP_ATTR_DISPLAY_NAME=cn
```

Group membership is read from `memberOf`. On OpenLDAP this needs the `memberof` overlay.

## SSO with Microsoft Entra ID

1. **Entra ID → App registrations → New registration**
   - Name `DMARCator`, *Accounts in this organizational directory only*
   - Redirect URI: platform **Web**, `https://dmarc.example.com/auth/oidc/callback`
     (exactly `BASE_URL` + `/auth/oidc/callback`)
2. **Certificates & secrets → New client secret**, then copy the value to `OIDC_CLIENT_SECRET`.
3. Decide how roles are assigned:
   - **App roles (recommended):** under *App roles → Create role*, create `DMARC.Admin` (and
     optionally `DMARC.User`). Then, under *Enterprise applications → DMARCator → Users and
     groups*, assign groups to the roles. Set `OIDC_ADMIN_GROUPS=DMARC.Admin`.
   - **Groups:** under *Token configuration → Add groups claim → Security groups* (ID token),
     add the claim, then set `OIDC_ADMIN_GROUPS=<group object ID>`. Users in more than 200
     groups get an overage link instead of a group list, so app roles are more reliable.
4. To restrict access to assigned users only, set **Assignment required = Yes** under
   *Enterprise applications → Properties*.
5. Configure:

```ini
OIDC_ENABLED=true
OIDC_DISCOVERY_URL=https://login.microsoftonline.com/<TENANT_ID>/v2.0/.well-known/openid-configuration
OIDC_CLIENT_ID=<Application (client) ID>
OIDC_CLIENT_SECRET=<secret>
OIDC_ADMIN_GROUPS=DMARC.Admin
BASE_URL=https://dmarc.example.com
COOKIE_SECURE=true
```

The account is identified by `preferred_username` (the UPN, e.g. `john.doe@example.com`).

## Other OIDC providers

| IdP | `OIDC_DISCOVERY_URL` | Notes |
|---|---|---|
| Keycloak | `https://sso.example.com/realms/<realm>/.well-known/openid-configuration` | Add a *Group Membership* mapper to the `groups` claim (full path off). |
| Okta | `https://<org>.okta.com/oauth2/default/.well-known/openid-configuration` | Add a `groups` claim to the ID token. |
| ADFS 2019+ | `https://adfs.example.com/adfs/.well-known/openid-configuration` | Issue a `groups` or `roles` claim. |
| Google Workspace | `https://accounts.google.com/.well-known/openid-configuration` | No group claims. Use `OIDC_USERNAME_CLAIM=email` and manage roles manually. |
