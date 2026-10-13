# Prihlasovanie a roly

DMARCula podporuje tri spôsoby prihlásenia, ktoré môžu byť zapnuté naraz:

| Spôsob | Kde sa zakladá účet | Rola |
|---|---|---|
| **Lokálny účet** | ručne v *Administrácia → Používatelia* (prvý admin z `.env`) | nastavuje admin |
| **LDAP / AD** | automaticky pri prvom prihlásení | podľa `LDAP_ADMIN_GROUP`, inak `DEFAULT_ROLE` |
| **SSO (OIDC)** | automaticky pri prvom prihlásení | podľa `OIDC_ADMIN_GROUPS`, inak `DEFAULT_ROLE` |

## Roly

| Rola | Oprávnenia |
|---|---|
| `viewer` | Všetky prehľady, domény, reporty, nástroje, export CSV, ručné spustenie DNS kontroly domény. |
| `admin` | Navyše správa poštových schránok, import reportov, správa používateľov, mazanie domén a ich dát. |

**Mapovanie skupín:** ak je nastavená skupina administrátorov (`LDAP_ADMIN_GROUP` alebo `OIDC_ADMIN_GROUPS`), rola externého účtu sa **prepočíta pri každom prihlásení**. Ručná zmena v rozhraní sa pri ďalšom prihlásení prepíše. Ak skupina nastavená nie je, rola sa určí iba pri vzniku účtu a potom ju mení admin ručne.

**Kolízia mien:** používateľské meno je jedinečné naprieč zdrojmi. Ak existuje lokálny účet `jan`, LDAP používateľ `jan` sa neprihlási, lebo sa skúsi lokálne heslo. Ak existuje SSO účet `jan@contoso.sk`, LDAP prihlásenie rovnakým menom sa odmietne. Zabraňuje to prevzatiu účtu cez iný zdroj.

**Deaktivácia:** zablokovaný účet (*aktívny* = vypnuté) sa neprihlási žiadnym spôsobom. Pre LDAP a SSO používateľov je spoľahlivejšie odobrať ich z povolenej skupiny.

## Lokálne účty

- Heslo musí mať aspoň 10 znakov a ukladá sa ako hash `scrypt` so soľou.
- Po 8 neúspešných pokusoch z jednej IP na jedno meno sa prihlásenie na 5 minút zablokuje.
- Ak chcete používať iba SSO, nastavte po vytvorení SSO admina `LOCAL_LOGIN_ENABLED=false`.

## LDAP / Active Directory

### Variant A – so servisným účtom (odporúčané)

```ini
LDAP_ENABLED=true
LDAP_SERVER=ldaps://dc01.contoso.local:636
LDAP_BIND_DN=CN=svc-dmarcula,OU=Service Accounts,DC=contoso,DC=local
LDAP_BIND_PASSWORD=********
LDAP_BASE_DN=DC=contoso,DC=local
LDAP_ALLOWED_GROUP=CN=DMARC-Users,OU=Groups,DC=contoso,DC=local
LDAP_ADMIN_GROUP=CN=DMARC-Admins,OU=Groups,DC=contoso,DC=local
```

Postup prihlásenia:
1. Aplikácia sa pripojí servisným účtom a podľa `LDAP_USER_FILTER` nájde **práve jedného** používateľa.
2. Heslo overí druhým bindom ako tento používateľ.
3. Skontroluje členstvo v `LDAP_ALLOWED_GROUP` a `LDAP_ADMIN_GROUP` (vrátane vnorených skupín).

Servisný účet stačí bežný doménový používateľ bez ďalších práv.

### Variant B – bez servisného účtu

```ini
LDAP_ENABLED=true
LDAP_SERVER=ldaps://dc01.contoso.local:636
LDAP_UPN_SUFFIX=contoso.local
LDAP_BASE_DN=DC=contoso,DC=local
```

Používateľ sa prihlási priamo ako `meno@contoso.local`. Môže zadať aj `meno`, `CONTOSO\meno` alebo celé UPN. Potom sa jeho vlastným účtom vyhľadajú atribúty a skupiny.

### Certifikáty

LDAPS overuje certifikát DC. Ak ho vydala interná CA, exportujte koreňový certifikát do PEM a nastavte `LDAP_CA_FILE=/cesta/ca.pem`. V Dockeri ho pripojte ako volume. `LDAP_TLS_VERIFY=false` používajte iba na testovanie.

### Iné LDAP servery (OpenLDAP, FreeIPA)

Upravte filter a atribúty, napr.:

```ini
LDAP_USER_FILTER=(&(objectClass=inetOrgPerson)(uid={username}))
LDAP_NESTED_GROUPS=false
LDAP_ATTR_DISPLAY_NAME=cn
```

Členstvo v skupinách sa číta z atribútu `memberOf`. Na OpenLDAP musí byť zapnutý overlay `memberof`.

## SSO cez Microsoft Entra ID

1. **Entra ID → App registrations → New registration**
   - Názov: `DMARCula`, *Accounts in this organizational directory only*
   - Redirect URI: **Web**, `https://dmarc.contoso.sk/auth/oidc/callback` (presne `BASE_URL` + `/auth/oidc/callback`)
2. **Certificates & secrets → New client secret** a hodnotu skopírujte do `OIDC_CLIENT_SECRET`.
3. Vyberte jeden z dvoch spôsobov určenia rolí:
   - **App roles (odporúčané):** *App roles → Create* rolu `DMARC.Admin` (a prípadne `DMARC.User`). V *Enterprise applications → DMARCula → Users and groups* priraďte skupiny k rolám. Potom nastavte `OIDC_ADMIN_GROUPS=DMARC.Admin`.
   - **Skupiny:** *Token configuration → Add groups claim → Security groups* (ID token). Potom `OIDC_ADMIN_GROUPS=<Object ID skupiny>`. Pri viac ako 200 skupinách Entra posiela namiesto zoznamu odkaz, preto sú app roles spoľahlivejšie.
4. Ak chcete pustiť iba priradených používateľov, zapnite v *Enterprise applications → Properties* voľbu **Assignment required = Yes**.
5. Konfigurácia:

```ini
OIDC_ENABLED=true
OIDC_DISCOVERY_URL=https://login.microsoftonline.com/<TENANT_ID>/v2.0/.well-known/openid-configuration
OIDC_CLIENT_ID=<Application (client) ID>
OIDC_CLIENT_SECRET=<secret>
OIDC_ADMIN_GROUPS=DMARC.Admin
BASE_URL=https://dmarc.contoso.sk
COOKIE_SECURE=true
```

Účet sa identifikuje claimom `preferred_username` (UPN), napr. `jan.novak@contoso.sk`.

## Iní poskytovatelia OIDC

| IdP | `OIDC_DISCOVERY_URL` | Poznámka |
|---|---|---|
| Keycloak | `https://sso.contoso.sk/realms/<realm>/.well-known/openid-configuration` | Pridajte mapper *Group Membership* do claimu `groups` (bez full path). |
| Okta | `https://<org>.okta.com/oauth2/default/.well-known/openid-configuration` | Pridajte claim `groups` do ID tokenu. |
| ADFS 2019+ | `https://adfs.contoso.sk/adfs/.well-known/openid-configuration` | Pravidlo pre claim `groups` alebo `roles`. |
| Google Workspace | `https://accounts.google.com/.well-known/openid-configuration` | Google neposiela skupiny. Použite `OIDC_USERNAME_CLAIM=email` a roly nastavte ručne. |
