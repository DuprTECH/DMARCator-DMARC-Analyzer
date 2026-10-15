# Microsoft 365 setup and report delivery

This page follows a report from the receiving provider to DMARCator:

**receivers → `rua=` address → Exchange Online mailbox → Microsoft Graph → DMARCator**

## 1. Report mailbox

Create a **shared mailbox** (needs no license), e.g. `dmarc@example.com`:

```powershell
Connect-ExchangeOnline
New-Mailbox -Shared -Name "DMARC reports" -PrimarySmtpAddress dmarc@example.com
```

Recommendations:
- Make sure reports don't land in Junk. Add a transport rule or an allow entry. Alternatively,
  set the folder in DMARCator to `JunkEmail`.
- If the mailbox also receives other mail, route reports into `Inbox/DMARC` with an inbox rule
  and set that folder in DMARCator.

## 2. App registration (Microsoft Graph)

1. **Entra ID → App registrations → New registration**: name `DMARCator-mail`, single tenant,
   no redirect URI.
2. **Certificates & secrets**, either:
   - a **client secret** (simpler, max. 24 months), or
   - a **certificate** (recommended). Upload the public part to Entra, then paste the private key
     (PEM) and the SHA-1 thumbprint into DMARCator under *Pokročilé* (Advanced):

     ```bash
     openssl req -x509 -newkey rsa:2048 -nodes -days 730 -subj "/CN=DMARCator" -keyout key.pem -out cert.pem
     openssl x509 -in cert.pem -noout -fingerprint -sha1 | sed 's/.*=//; s/://g'   # thumbprint
     ```
3. Note the **Application (client) ID**, the **Directory (tenant) ID**, and the **Object ID**
   shown under *Enterprise applications*. That's a different object ID from the one under App registrations.

## 3. Restrict the app to one mailbox (RBAC for Applications)

The Graph permission `Mail.ReadWrite` granted in Entra ID applies to **every mailbox in the
tenant**. That's far too broad for an app that needs a single mailbox. Use **Exchange Online
RBAC for Applications** to scope it:

```powershell
Connect-ExchangeOnline

# 1) register the service principal in Exchange (ObjectId = Enterprise application → Object ID)
New-ServicePrincipal -AppId <ClientId> -ObjectId <EnterpriseAppObjectId> -DisplayName "DMARCator"

# 2) a scope containing only the report mailbox
New-ManagementScope -Name "DMARCator-scope" -RecipientRestrictionFilter "PrimarySmtpAddress -eq 'dmarc@example.com'"

# 3) grant the role within that scope
New-ManagementRoleAssignment -App <ClientId> -Role "Application Mail.ReadWrite" -CustomResourceScope "DMARCator-scope"

# 4) verify – InScope must be True for the report mailbox and False for anything else
Test-ServicePrincipalAuthorization -Identity <ClientId> -Resource dmarc@example.com
Test-ServicePrincipalAuthorization -Identity <ClientId> -Resource ceo@example.com
```

With RBAC for Applications, **do not** add the `Mail.ReadWrite` Graph permission in the app
registration. If it's already there, remove it, otherwise tenant-wide access remains. RBAC
changes can take 30 minutes to 2 hours to apply.

> **Legacy alternative:** grant the Graph `Mail.ReadWrite` application permission with admin
> consent and limit it with
> `New-ApplicationAccessPolicy -AccessRight RestrictAccess -AppId <ClientId> -PolicyScopeGroupId <mail-enabled security group>`.
> Microsoft is replacing this with RBAC for Applications.

**Which role to use:**

| Behaviour | Role |
|---|---|
| move processed messages and/or mark them read (default) | `Application Mail.ReadWrite` |
| read only (turn off "mark as read", "only unread", and moving) | `Application Mail.Read` |

## 4. Add the mailbox in DMARCator

*Administrácia → Poštové schránky → Exchange Online (Graph)*:

| Field | Value |
|---|---|
| Tenant ID / Client ID | from step 2 |
| Client secret | or a certificate under *Pokročilé* |
| Mailbox | `dmarc@example.com` |
| Report folder | `Inbox` |
| Move processed to | `Inbox/DMARC-processed` |

Click **Test**. It should show the message count of the folder. Then click **Spustiť** (Run),
or wait for the next scheduled run (`POLL_INTERVAL_MINUTES`).

On a mailbox that already holds a backlog, at most 500 messages are processed per run. The
rest follow in later runs.

## 5. Alternative: IMAP with OAuth2 (Exchange Online)

If you prefer IMAP over Graph:

```powershell
New-ServicePrincipal -AppId <ClientId> -ObjectId <EnterpriseAppObjectId>
Add-MailboxPermission -Identity dmarc@example.com -User <EnterpriseAppObjectId> -AccessRights FullAccess
```

In the app registration, add **Office 365 Exchange Online → Application → `IMAP.AccessAsApp`**
and grant admin consent. In DMARCator, choose IMAP with `outlook.office365.com:993`, SSL,
authentication *OAuth2 – Exchange Online*, username `dmarc@example.com`, and the tenant ID,
client ID and secret.

Exchange Online doesn't allow IMAP with a password (basic auth). Password login is meant for
other mail servers (Gmail with an app password, Dovecot, Zimbra, …).

### Sending e-mail alerts

To send *E-mail alerts* through Microsoft 365, the mailbox app needs **Mail.Send**. With RBAC for
Applications grant it only for the report mailbox:

```powershell
New-ManagementRoleAssignment -App <ClientId> -Role "Application Mail.Send" -CustomResourceScope "DMARCator-scope"
```

(or the Graph application permission `Mail.Send` with admin consent, which covers every mailbox).
Then choose the mailbox as the sender in *Administration → E-mail alerts*.

### Importing tenant domains

*Domains → Add domains manually → Import domains from Microsoft 365* reads every verified domain of
the tenant (`GET /domains`) with the app of the Exchange Online mailbox. Add the Microsoft Graph
**application** permission **`Domain.Read.All`** to that app registration and **grant admin
consent**. It only reads domain names; it changes nothing. Without it the import shows
`HTTP 403 Authorization_RequestDenied`. The domains are then checked daily like all others.

To keep the list up to date automatically, tick **Import domains from Microsoft 365 automatically**
in the mailbox settings. The domains are read right after saving and then with every daily DNS
check; new ones are checked in the same run. Domains removed from the tenant stay in DMARCator
with their report history. The result (or the error) appears in the mailbox processing log.

## 6. DNS records for your domains

| Record | Value | Purpose |
|---|---|---|
| `_dmarc.example.com TXT` | `v=DMARC1; p=none; rua=mailto:dmarc@example.com; ruf=mailto:dmarc@example.com; fo=1` | Start with `p=none` and collect reports. |
| `_smtp._tls.example.com TXT` | `v=TLSRPTv1; rua=mailto:dmarc@example.com` | TLS-RPT reports about delivery to your MX. |
| `_mta-sts.example.com TXT` + `https://mta-sts.example.com/.well-known/mta-sts.txt` | `v=STSv1; id=20261013` | Enforce TLS for inbound delivery (optional). |

**Reports for other domains.** With 140 domains, the report mailbox's domain usually differs
from most of the domains being monitored. Each monitored domain must then be **authorized by the domain of the
report address**. Otherwise most receivers won't send the reports. If `dmarc@example.com` collects
reports for `example.org`, `example.net`, …, publish in the **example.com** zone:

```
example.org._report._dmarc.example.com  TXT  "v=DMARC1"
example.net._report._dmarc.example.com  TXT  "v=DMARC1"
```

or a single wildcard for all of them:

```
*._report._dmarc.example.com  TXT  "v=DMARC1"
```

DMARCator checks this record for every domain and flags missing ones on the domain page.

Domains don't have to be registered anywhere in DMARCator. Each one appears automatically with its first report.

## 7. DKIM in Microsoft 365

If DMARCator shows *Microsoft 365* in the **Nesúlad** (non-compliant) category with a DKIM
domain of `*.onmicrosoft.com`, custom DKIM signing is not enabled:

```powershell
New-DkimSigningConfig -DomainName example.com -Enabled $false
Get-DkimSigningConfig example.com | Format-List Selector1CNAME, Selector2CNAME
# create CNAMEs selector1._domainkey and selector2._domainkey as shown, then:
Set-DkimSigningConfig -Identity example.com -Enabled $true
```

For many domains, loop over them:

```powershell
Get-AcceptedDomain | ForEach-Object {
    if (-not (Get-DkimSigningConfig $_.DomainName -ErrorAction SilentlyContinue)) {
        New-DkimSigningConfig -DomainName $_.DomainName -Enabled $false | Out-Null
    }
    Get-DkimSigningConfig $_.DomainName | Select-Object Domain, Enabled, Selector1CNAME, Selector2CNAME
} | Export-Csv dkim-cnames.csv -NoTypeInformation
```

## Troubleshooting

| Error in the log | Cause |
|---|---|
| `Token: invalid_client` | Wrong or expired client secret, or a wrong certificate thumbprint. |
| `Token: unauthorized_client` | Wrong tenant ID or client ID. |
| `HTTP 403 ErrorAccessDenied` | No permission on the mailbox. Check RBAC scope / admin consent, and wait for propagation. |
| `HTTP 404 ErrorInvalidUser` / `MailboxNotEnabledForRESTAPI` | Wrong mailbox address, or the mailbox is on-premises. |
| `Priečinok 'X' neexistuje` | Folder paths start at the mailbox root, e.g. `Inbox/DMARC`. |
| `správa neobsahuje DMARC/TLS report` | The message had no recognizable report attachment (e.g. a regular e-mail). It's harmless. |
