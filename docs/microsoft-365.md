# Microsoft 365 a príjem reportov

Tento návod popisuje cestu reportu od poskytovateľa až do aplikácie:

**poskytovatelia → `rua=` adresa → schránka v Exchange Online → Microsoft Graph → DMARCula**

## 1. Schránka na reporty

Vytvorte **zdieľanú schránku** (nepotrebuje licenciu), napr. `dmarc@contoso.sk`:

```powershell
Connect-ExchangeOnline
New-Mailbox -Shared -Name "DMARC reports" -PrimarySmtpAddress dmarc@contoso.sk
```

Odporúčania k schránke:
- Vypnite pre ňu spam filter alebo pridajte pravidlo, aby reporty nekončili v Junk. Prípadne nastavte ako priečinok v aplikácii `JunkEmail`.
- Ak cez schránku chodí aj iná pošta, presúvajte reporty pravidlom do priečinka `Inbox/DMARC` a v aplikácii nastavte tento priečinok.

## 2. Registrácia aplikácie (Microsoft Graph)

1. **Entra ID → App registrations → New registration**, názov `DMARCula-mail`, single tenant, bez redirect URI.
2. **Certificates & secrets**:
   - **client secret** (jednoduchšie, platí max. 24 mesiacov), alebo
   - **certifikát** (odporúčané). Vygenerujte ho, verejnú časť nahrajte do Entra a privátny kľúč (PEM) a SHA-1 thumbprint zadajte v aplikácii v sekcii *Pokročilé*.

     ```bash
     openssl req -x509 -newkey rsa:2048 -nodes -days 730 -subj "/CN=DMARCula" -keyout key.pem -out cert.pem
     openssl x509 -in cert.pem -noout -fingerprint -sha1 | tr -d ':'   # thumbprint
     ```
3. Poznačte si **Application (client) ID**, **Directory (tenant) ID** a **Object ID** zo stránky *Enterprise applications* (nie z App registration).

## 3. Oprávnenie iba na jednu schránku (RBAC for Applications)

Graph povolenie `Mail.ReadWrite` priradené v Entra ID platí pre **všetky schránky v tenante**. Pre aplikáciu, ktorá potrebuje jedinú schránku, je to zbytočne veľa. Správny postup je **Exchange Online RBAC for Applications**:

```powershell
Connect-ExchangeOnline

# 1) service principal v Exchange (ObjectId = Enterprise application → Object ID)
New-ServicePrincipal -AppId <ClientId> -ObjectId <EnterpriseAppObjectId> -DisplayName "DMARCula"

# 2) scope obmedzený na DMARC schránku
New-ManagementScope -Name "DMARCula-scope" -RecipientRestrictionFilter "PrimarySmtpAddress -eq 'dmarc@contoso.sk'"

# 3) priradenie roly v rámci scope
New-ManagementRoleAssignment -App <ClientId> -Role "Application Mail.ReadWrite" -CustomResourceScope "DMARCula-scope"

# 4) overenie – InScope musí byť True
Test-ServicePrincipalAuthorization -Identity <ClientId> -Resource dmarc@contoso.sk
Test-ServicePrincipalAuthorization -Identity <ClientId> -Resource ceo@contoso.sk     # musí byť False
```

V tomto prípade **nepridávajte** Graph povolenie `Mail.ReadWrite` v App registration. Ak tam už je, odstráňte ho, inak by prístup k celému tenantu zostal. Zmena RBAC sa môže prejaviť až po 30 minútach až 2 hodinách.

> **Alternatíva (staršia):** Graph povolenie `Mail.ReadWrite` s admin consent a `New-ApplicationAccessPolicy -AccessRight RestrictAccess -AppId <ClientId> -PolicyScopeGroupId <mail-enabled security group>`. Microsoft ju nahrádza mechanizmom RBAC for Applications.

**Ktoré oprávnenie zvoliť:**

| Požiadavka | Rola |
|---|---|
| presúvať spracované správy alebo ich označovať ako prečítané (predvolené) | `Application Mail.ReadWrite` |
| iba čítať (vypnite „Označiť ako prečítané“, „Iba neprečítané“ a presúvanie) | `Application Mail.Read` |

## 4. Pridanie schránky v aplikácii

*Administrácia → Poštové schránky → Exchange Online (Graph)*:

| Pole | Hodnota |
|---|---|
| Tenant ID / Client ID | z kroku 2 |
| Client secret | alebo certifikát v sekcii *Pokročilé* |
| Schránka | `dmarc@contoso.sk` |
| Priečinok s reportmi | `Inbox` |
| Presunúť spracované do | `Inbox/DMARC-processed` |

Kliknite na **Test**. Mal by sa zobraziť počet správ v priečinku. Potom kliknite na **Spustiť**, alebo počkajte na automatický beh (`POLL_INTERVAL_MINUTES`).

Pri prvom spustení na schránke, kde už reporty sú, sa spracuje najviac 500 správ za jeden beh. Zvyšok sa spracuje v ďalších behoch.

## 5. Alternatíva: IMAP s OAuth2 (Exchange Online)

Ak nechcete Graph:

```powershell
New-ServicePrincipal -AppId <ClientId> -ObjectId <EnterpriseAppObjectId>
Add-MailboxPermission -Identity dmarc@contoso.sk -User <EnterpriseAppObjectId> -AccessRights FullAccess
```

V App registration pridajte povolenie **Office 365 Exchange Online → Application → `IMAP.AccessAsApp`** a udeľte admin consent. V aplikácii zvoľte IMAP s nastavením `outlook.office365.com:993`, SSL, autentifikácia *OAuth2 – Exchange Online*, používateľ `dmarc@contoso.sk` a tenant, client ID a secret.

IMAP s heslom (basic auth) Exchange Online nepodporuje. Funguje pre iné poštové servery (Gmail s heslom aplikácie, Zimbra, Dovecot…).

## 6. DNS záznamy pre vaše domény

| Záznam | Hodnota | Účel |
|---|---|---|
| `_dmarc.contoso.sk TXT` | `v=DMARC1; p=none; rua=mailto:dmarc@contoso.sk; ruf=mailto:dmarc@contoso.sk; fo=1` | Začnite s `p=none` a zbierajte reporty. |
| `_smtp._tls.contoso.sk TXT` | `v=TLSRPTv1; rua=mailto:dmarc@contoso.sk` | TLS-RPT reporty o doručovaní k vašim MX. |
| `_mta-sts.contoso.sk TXT` + `https://mta-sts.contoso.sk/.well-known/mta-sts.txt` | `v=STSv1; id=20261013` | Vynútenie TLS pri doručovaní (voliteľné). |

**Reporty pre inú doménu:** ak `fabrikam.sk` posiela reporty na `dmarc@contoso.sk`, musí doména **contoso.sk** povoliť ich príjem:

```
fabrikam.sk._report._dmarc.contoso.sk  TXT  "v=DMARC1"
```

Bez neho väčšina poskytovateľov reporty nepošle. Aplikácia tento záznam kontroluje a chýbajúci ukáže v detaile domény.

Ďalšie domény na sledovanie netreba nikde zadávať. Pridajú sa automaticky pri prvom prijatom reporte.

## 7. DKIM v Microsoft 365

Ak aplikácia ukazuje odosielateľa *Microsoft 365* v kategórii **Nesúlad** s DKIM doménou `*.onmicrosoft.com`, nemáte zapnutý vlastný DKIM podpis:

```powershell
New-DkimSigningConfig -DomainName contoso.sk -Enabled $false
Get-DkimSigningConfig contoso.sk | Format-List Selector1CNAME, Selector2CNAME
# vytvorte CNAME selector1._domainkey a selector2._domainkey podľa výstupu, potom:
Set-DkimSigningConfig -Identity contoso.sk -Enabled $true
```
