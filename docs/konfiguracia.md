# Konfigurácia

Aplikácia sa konfiguruje premennými prostredia alebo súborom `.env` v pracovnom adresári. Vzor je v [`.env.example`](../.env.example). Názvy premenných nerozlišujú veľké a malé písmená. Logické hodnoty sú `true` a `false`.

Poštové schránky sa **nekonfigurujú** cez `.env`, ale v rozhraní (*Administrácia → Poštové schránky*). Ich prístupové údaje sa ukladajú do databázy šifrované.

## Základ

| Premenná | Predvolené | Popis |
|---|---|---|
| `APP_NAME` | `DMARCula` | Názov v rozhraní a v titulku stránok. |
| `BASE_URL` | `http://localhost:8000` | Verejná adresa aplikácie. Používa sa na SSO redirect URI. |
| `SECRET_KEY` | – (**povinné**) | Podpisuje session cookie a z neho sa odvodzuje šifrovací kľúč pre údaje schránok. Aspoň 16 znakov, odporúča sa 48+ náhodných. Po zmene sa všetci odhlásia a heslá schránok treba zadať znova. |
| `DATABASE_URL` | `sqlite:///./data/dmarcula.db` | SQLAlchemy URL. Pre PostgreSQL: `postgresql+psycopg://user:heslo@host:5432/dmarcula`. |
| `SESSION_MAX_AGE_HOURS` | `12` | Platnosť prihlásenia. |
| `COOKIE_SECURE` | `false` | `true`, ak aplikácia beží za HTTPS (odporúčané). |

## Lokálne účty

| Premenná | Predvolené | Popis |
|---|---|---|
| `ADMIN_USERNAME` | `admin` | Meno prvého administrátora. |
| `ADMIN_PASSWORD` | – | Heslo prvého administrátora. Použije sa iba vtedy, keď v DB nie je žiadny používateľ. |
| `LOCAL_LOGIN_ENABLED` | `true` | `false` vypne prihlasovanie lokálnymi účtami, napr. keď sa používa iba SSO. Pozor, vypne aj lokálneho admina. |
| `DEFAULT_ROLE` | `viewer` | Rola nových LDAP/SSO používateľov, ak nepatria do skupiny administrátorov. |

## LDAP / Active Directory

| Premenná | Predvolené | Popis |
|---|---|---|
| `LDAP_ENABLED` | `false` | Zapne prihlasovanie cez LDAP. |
| `LDAP_SERVER` | – | `ldaps://dc01.contoso.local:636` alebo `ldap://…:389` (vtedy použite StartTLS). |
| `LDAP_START_TLS` | `false` | StartTLS na porte 389. |
| `LDAP_TLS_VERIFY` | `true` | Overovanie certifikátu servera. |
| `LDAP_CA_FILE` | – | Cesta k PEM súboru s internou CA. |
| `LDAP_BIND_DN` | – | DN servisného účtu na vyhľadávanie používateľov. Ak je prázdne, aplikácia sa prihlási priamo ako používateľ. |
| `LDAP_BIND_PASSWORD` | – | Heslo servisného účtu. |
| `LDAP_UPN_SUFFIX` | – | Pri prihlásení bez servisného účtu sa doplní k menu (`jan` → `jan@contoso.local`). |
| `LDAP_BASE_DN` | – | Kde sa hľadajú používatelia, napr. `DC=contoso,DC=local`. |
| `LDAP_USER_FILTER` | `(&(objectClass=user)(\|(sAMAccountName={username})(userPrincipalName={username})))` | Vyhľadávací filter. `{username}` sa nahradí escapovaným menom. |
| `LDAP_ALLOWED_GROUP` | – | DN skupiny s prístupom. Ak je prázdne, prihlási sa každý platný účet. |
| `LDAP_ADMIN_GROUP` | – | DN skupiny administrátorov. Ak je nastavená, rola sa prepočíta pri každom prihlásení. |
| `LDAP_NESTED_GROUPS` | `true` | Vyhodnocuje aj vnorené skupiny (AD `LDAP_MATCHING_RULE_IN_CHAIN`). |
| `LDAP_ATTR_DISPLAY_NAME` | `displayName` | Atribút s celým menom. |
| `LDAP_ATTR_MAIL` | `mail` | Atribút s e-mailom. |

## SSO (OpenID Connect)

| Premenná | Predvolené | Popis |
|---|---|---|
| `OIDC_ENABLED` | `false` | Zapne tlačidlo SSO na prihlasovacej stránke. |
| `OIDC_DISCOVERY_URL` | – | URL `.well-known/openid-configuration`. Pre Entra ID: `https://login.microsoftonline.com/<TENANT_ID>/v2.0/.well-known/openid-configuration` |
| `OIDC_CLIENT_ID` | – | Client ID aplikácie. |
| `OIDC_CLIENT_SECRET` | – | Client secret. |
| `OIDC_SCOPES` | `openid profile email` | Požadované scopes. |
| `OIDC_BUTTON_LABEL` | `Prihlásiť cez Microsoft` | Text tlačidla. |
| `OIDC_USERNAME_CLAIM` | `preferred_username` | Claim s menom používateľa (náhrada: `upn`, `email`, `sub`). |
| `OIDC_GROUPS_CLAIM` | `groups` | Claim so skupinami. Claim `roles` (Entra app roles) sa číta vždy. |
| `OIDC_ALLOWED_GROUPS` | – | Čiarkou oddelené ID skupín alebo názvy rolí s prístupom. Ak je prázdne, prihlási sa každý z IdP. |
| `OIDC_ADMIN_GROUPS` | – | Čiarkou oddelené ID skupín alebo rolí administrátorov. |

## Spracovanie reportov

| Premenná | Predvolené | Popis |
|---|---|---|
| `POLL_INTERVAL_MINUTES` | `15` | Ako často sa sťahujú všetky povolené schránky. |
| `DNS_REFRESH_HOURS` | `24` | Ako často sa obnovuje DNS kontrola všetkých domén. Nové domény sa kontrolujú hneď po prvom reporte. |
| `RESOLVE_PTR` | `true` | Reverzné DNS zdrojových IP. Potrebné na rozpoznanie odosielajúcich služieb. |
| `DNS_SERVERS` | – | Vlastné DNS resolvery oddelené čiarkou. Ak je prázdne, použijú sa systémové. |
| `GEOIP_COUNTRY_DB` | – | Cesta k `GeoLite2-Country.mmdb`. |
| `GEOIP_ASN_DB` | – | Cesta k `GeoLite2-ASN.mmdb`. |
| `MAX_ATTACHMENT_MB` | `50` | Maximálna veľkosť správy alebo nahraného súboru. Rozbalené archívy majú pevný limit 200 MB. |

## Nastavenia poštovej schránky (v rozhraní)

| Pole | Graph | IMAP | Popis |
|---|:-:|:-:|---|
| Tenant ID, Client ID | ✔ | OAuth2 | Údaje z registrácie aplikácie v Entra ID. |
| Client secret / certifikát (PEM + thumbprint) | ✔ | secret | Spôsob overenia aplikácie. |
| Schránka | ✔ | – | UPN alebo SMTP adresa (môže byť zdieľaná schránka). |
| Server, port, zabezpečenie, používateľ, heslo | – | ✔ | Parametre IMAP. |
| Priečinok s reportmi | ✔ | ✔ | `Inbox`, `Inbox/DMARC`… (IMAP: `INBOX`). |
| Presunúť spracované do | ✔ | ✔ | Ak priečinok neexistuje, vytvorí sa. Prázdne = nepresúvať. |
| Presunúť chybné do | ✔ | ✔ | Správy bez platného reportu. |
| Iba neprečítané / označiť ako prečítané | ✔ | ✔ | Pri vypnutom „iba neprečítané“ sa spracuje celý priečinok (duplicity sa preskočia). |
| Authority host, Graph endpoint | ✔ | – | Pre národné cloudy (GCC High, 21Vianet). |
