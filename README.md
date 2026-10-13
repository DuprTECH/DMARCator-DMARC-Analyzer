# DMARCula – DMARC Analyzer

DMARCula je webová aplikácia, ktorá sama sťahuje DMARC reporty zo schránky v Exchange Online (Microsoft 365), z IMAP alebo z ručne nahratých súborov. Reporty rozparsuje, ku každému odosielateľovi dohľadá službu (Microsoft 365, SendGrid, Mailchimp…), skontroluje DNS konfiguráciu domén a výsledky ukáže na dashboarde.
Pri návrhu som vychádzal z existujúcich riešení, ako sú dmarcian, Valimail, EasyDMARC a parsedmarc.

## Funkcie

| Oblasť | Čo vie |
|---|---|
| **Prihlásenie** | lokálne účty, **LDAP / Active Directory** (LDAPS/StartTLS, servisný účet alebo priamy UPN bind, filter podľa skupiny, vnorené skupiny), **SSO cez OpenID Connect** (Entra ID, Keycloak, Okta, ADFS…). Roly `admin` a `viewer` sa môžu odvodzovať zo skupín. |
| **Ingest** | **Microsoft Graph** (EXO, iba aplikačné oprávnenie, client secret alebo certifikát, obmedzenie cez RBAC for Applications), **IMAP** (heslo alebo OAuth2 XOAUTH2 pre EXO), ručný upload (.xml, .gz, .zip, .json, .eml). Spracované správy vie označiť ako prečítané a presunúť do priečinka, duplicitné reporty preskočí. |
| **Reporty** | agregované (RUA) vrátane DMARC 2.0 namespace, forenzné (RUF/ARF), SMTP TLS (TLS-RPT, RFC 8460) |
| **Klasifikácia** | V súlade / Preposlané / Nesúlad (legitímny zdroj) / Hrozba – po vzore dmarcianu. Rozpozná vyše 60 známych odosielajúcich služieb a M365 tenant bez vlastného DKIM. |
| **Dashboard** | KPI s porovnaním voči predchádzajúcemu obdobiu, denný objem, miera súladu v čase, zarovnanie SPF/DKIM, aplikovaná politika, reportéri, krajiny (mapa), služby, domény so sparkline grafmi, nové zlyhávajúce zdroje, odporúčania |
| **Domény** | DNS skóre A–F: DMARC (vrátane overenia externých rua cieľov), SPF (rekurzívny počet lookupov, void lookupy, +all/ptr), DKIM (selektory z reportov a bežné selektory, dĺžka kľúča), MX, MTA-STS (stiahne a overí politiku), TLS-RPT, BIMI. K tomu **pripravenosť na p=quarantine / p=reject**. |
| **Nástroje** | kontrola ľubovoľnej domény a generátor DMARC záznamu, export zdrojov do CSV, tmavý režim, responzívne rozloženie |

## Rýchly štart

### Docker (odporúčané, PostgreSQL)

```bash
cp .env.example .env      # vyplňte SECRET_KEY, ADMIN_PASSWORD, BASE_URL …
docker compose up -d --build
```

Aplikácia beží na `http://localhost:8000`. Pred ňu dajte reverzný proxy s HTTPS a nastavte `COOKIE_SECURE=true`.

### Lokálne (SQLite)

```bash
python -m venv .venv && .venv/Scripts/activate      # Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                                  # nastavte aspoň SECRET_KEY a ADMIN_PASSWORD
python -m app.demo --days 60                          # voliteľné: demo dáta
uvicorn app.main:app --port 8000
```

Spúšťajte iba **jeden worker**, pretože plánovač sťahovania beží v rámci procesu.

## Napojenie na Exchange Online (Microsoft Graph)

1. **Entra ID → App registrations → New registration** (single tenant).
2. **API permissions → Microsoft Graph → Application → `Mail.ReadWrite`**, potom *Grant admin consent*. Ak správy nepresúvate ani neoznačujete, stačí `Mail.Read`.
3. **Certificates & secrets**: vytvorte client secret alebo nahrajte certifikát.
4. **Obmedzte aplikáciu iba na DMARC schránku.** Bez tohto kroku má aplikácia prístup ku všetkým schránkam v tenante. Postup cez Exchange Online PowerShell (RBAC for Applications):

   ```powershell
   Connect-ExchangeOnline
   New-ServicePrincipal -AppId <ClientId> -ObjectId <EnterpriseAppObjectId> -DisplayName "DMARCula"
   New-ManagementScope -Name "DMARCula-scope" -RecipientRestrictionFilter "PrimarySmtpAddress -eq 'dmarc@contoso.sk'"
   New-ManagementRoleAssignment -App <ClientId> -Role "Application Mail.ReadWrite" -CustomResourceScope "DMARCula-scope"
   Test-ServicePrincipalAuthorization -Identity <ClientId> -Resource dmarc@contoso.sk
   ```

   Ak použijete RBAC for Applications, **odoberte** tenantové Graph oprávnenie `Mail.ReadWrite` z App registration. Inak by stále platilo pre všetky schránky.
5. V aplikácii otvorte **Administrácia → Poštové schránky → Exchange Online (Graph)**, vyplňte Tenant ID, Client ID, secret a schránku a kliknite na **Test**.

Do DNS potom pridajte `rua=mailto:dmarc@contoso.sk` (prípadne aj `ruf=`) a pre TLS-RPT záznam `_smtp._tls TXT "v=TLSRPTv1; rua=mailto:dmarc@contoso.sk"`.
Ak reporty prichádzajú do schránky v inej doméne, každá sledovaná doména potrebuje autorizačný záznam `<domena>._report._dmarc.<cielova-domena> TXT "v=DMARC1"`. Aplikácia jeho prítomnosť kontroluje.

**IMAP + OAuth2 (EXO):** postup je rovnaký, len namiesto Graph oprávnenia priraďte rolu `Application EWS/IMAP` (`IMAP.AccessAsApp`) a schránke cez `Add-MailboxPermission` dajte FullAccess pre service principal.

## SSO cez Entra ID (OIDC)

1. App registration s redirect URI typu **Web**: `https://<BASE_URL>/auth/oidc/callback`.
2. Vytvorte client secret. V **Token configuration** pridajte *groups claim* (Security groups), prípadne definujte **App roles**.
3. V `.env` nastavte:
   ```
   OIDC_ENABLED=true
   OIDC_DISCOVERY_URL=https://login.microsoftonline.com/<TENANT_ID>/v2.0/.well-known/openid-configuration
   OIDC_CLIENT_ID=… / OIDC_CLIENT_SECRET=…
   OIDC_ADMIN_GROUPS=<objectId skupiny adminov alebo názov app role>
   ```

## LDAP / Active Directory

Nastavte `LDAP_ENABLED=true`, `LDAP_SERVER=ldaps://…`, `LDAP_BASE_DN` a jeden z týchto spôsobov:
- **servisný účet:** `LDAP_BIND_DN` a `LDAP_BIND_PASSWORD`; aplikácia nájde používateľa a overí jeho heslo druhým bindom,
- **bez servisného účtu:** `LDAP_UPN_SUFFIX=contoso.local`; používateľ sa prihlási priamo cez `meno@contoso.local`.

Prístup obmedzíte cez `LDAP_ALLOWED_GROUP`, administrátorov určíte cez `LDAP_ADMIN_GROUP` (DN). Vnorené skupiny sa vyhodnocujú cez `LDAP_MATCHING_RULE_IN_CHAIN`.
Lokálny účet má prednosť pred LDAP účtom s rovnakým menom. Účet z iného zdroja (napr. SSO) s rovnakým menom sa prihlásiť nedá.

## GeoIP (voliteľné)

Stiahnite si bezplatné databázy MaxMind GeoLite2 `GeoLite2-Country.mmdb` a `GeoLite2-ASN.mmdb` (vyžaduje registráciu), uložte ich do `./geoip` a nastavte `GEOIP_COUNTRY_DB=/geoip/GeoLite2-Country.mmdb` a `GEOIP_ASN_DB=/geoip/GeoLite2-ASN.mmdb`.

## Architektúra

```
app/
  main.py            FastAPI, session middleware, plánovač (APScheduler)
  auth/              LDAP (ldap3), OIDC (authlib)
  ingest/            parser (RUA/RUF/TLS-RPT, zip/gz/eml), graph.py (EXO), imap.py, service.py (ukladanie, deduplikácia)
  analysis/          dns_checks.py, senders.py (klasifikácia), enrich.py (PTR, GeoIP), stats.py (agregácie, odporúčania)
  routes/            stránky, administrácia, prihlásenie
  templates/, static/  Jinja2 + ECharts, vlastné CSS so svetlou a tmavou témou
tests/               pytest – parser a celý tok aplikácie
```

**Bezpečnosť:** heslá sú hashované cez scrypt. Prístupové údaje ku schránkam sú v DB šifrované (Fernet, kľúč odvodený zo `SECRET_KEY`). Aplikácia má CSRF ochranu všetkých POST požiadaviek, obmedzenie počtu pokusov o prihlásenie, bezpečnostné hlavičky, `defusedxml` proti XXE a limity veľkosti archívov proti zip-bombám.

**Poznámka:** grafy (ECharts) a mapa sveta sa načítavajú z CDN jsDelivr. V izolovanej sieti si ich stiahnite do `app/static` a upravte cestu v `templates/base.html`.

## Testy

```bash
pip install pytest
pytest
```
