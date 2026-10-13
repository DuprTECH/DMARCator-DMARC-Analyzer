# Architektúra

## Prehľad komponentov

```
                    ┌──────────────── DMARCula (1 proces, uvicorn) ────────────────┐
 Prehliadač ──HTTPS─┤ FastAPI routes ── Jinja2 šablóny + ECharts (CDN)             │
                    │   │  auth: lokálne (scrypt) · LDAP (ldap3) · OIDC (authlib)  │
                    │   ▼                                                          │
                    │ analysis/stats ◄── SQLAlchemy ──► SQLite / PostgreSQL        │
                    │   ▲                                                          │
                    │ APScheduler ─► ingest/service ─► parser ─► enrich ─► store   │
                    │                 │        │                    │              │
                    └─────────────────┼────────┼────────────────────┼──────────────┘
                                      ▼        ▼                    ▼
                           Microsoft Graph   IMAP          DNS (PTR, TXT, MX), GeoLite2,
                           (Exchange Online)               HTTPS (MTA-STS politika)
```

| Modul | Súbor | Zodpovednosť |
|---|---|---|
| Aplikácia | `app/main.py` | FastAPI, session middleware, bezpečnostné hlavičky, obsluha chýb, plánovač, bootstrap admina |
| Konfigurácia | `app/config.py` | `pydantic-settings`, načítanie `.env` |
| Databáza | `app/db.py`, `app/models.py` | engine, session, SQLite WAL a SAVEPOINT, ORM modely |
| Bezpečnosť | `app/security.py` | hash hesiel, šifrovanie konfigurácie (Fernet), CSRF, throttling, závislosti `current_user` a `admin_required` |
| Prihlásenie | `app/auth/ldap_auth.py`, `app/auth/oidc.py`, `app/routes/auth.py` | LDAP bind a vyhľadanie, OIDC klient, založenie a aktualizácia externých účtov |
| Ingest | `app/ingest/graph.py`, `imap.py` | čítanie schránok, resolve a vytváranie priečinkov, mark read / move, throttling (429) |
| Parser | `app/ingest/parser.py` | MIME, zip/gzip, aggregate XML (aj DMARC 2.0 namespace), ARF, TLS-RPT JSON |
| Ukladanie | `app/ingest/service.py` | deduplikácia, obohatenie, klasifikácia, log, spúšťanie zdrojov a DNS kontrol |
| Analýza | `app/analysis/*` | `senders` (služby a kategórie), `enrich` (PTR, GeoIP s cache), `dns_checks`, `stats` (agregácie a odporúčania) |
| Web | `app/routes/pages.py`, `admin.py`, `app/web.py`, `templates/`, `static/` | stránky, administrácia, Jinja filtre, CSS tokeny, ECharts helpery |

## Tok spracovania reportu

1. **Plánovač** (`run_all_sources`) beží každých `POLL_INTERVAL_MINUTES`. Každý zdroj má zámok, takže ručné a plánované spustenie sa neprekrývajú.
2. **Schránka** vráti surový MIME správ (Graph: `GET /messages/{id}/$value`, IMAP: `BODY.PEEK[]`), najviac 500 za beh.
3. **Parser** rozpozná typ obsahu:
   - `multipart/report; report-type=feedback-report` → forenzný report,
   - prílohy zip/gz/xml/json → rozbalenie (limit 200 MB) → `<feedback>` = agregovaný report, JSON s `policies` = TLS-RPT.
4. **Ukladanie** (`store`) pre každý report:
   - deduplikácia: agregované podľa `(org_name, report_id, domain)`, TLS podľa `(org, report_id, policy_domain)`, forenzné podľa SHA-256 hlavičiek,
   - obohatenie IP adries (PTR, krajina, ASN). Výsledok sa cachuje v `ip_info` na 14 dní a vyhodnocuje paralelne (16 vlákien),
   - identifikácia služby (`identify`) a kategória (`categorize`),
   - založenie alebo aktualizácia domény.
   Každý report sa ukladá v SAVEPOINT-e, takže chyba jedného nezahodí ostatné.
5. Správa sa označí ako prečítaná alebo sa presunie. Výsledok sa zapíše do `ingest_log` a `mail_sources.last_*`.
6. Pre **nové domény** sa hneď spustí DNS kontrola. Všetky domény sa obnovujú raz za `DNS_REFRESH_HOURS`.

## Dátový model

```
users                 mail_sources (config_encrypted)       ingest_log
domains ─┐            ip_info (cache PTR/GeoIP)
         │ (podľa názvu)
aggregate_reports 1───* aggregate_records
forensic_reports      tls_reports
```

| Tabuľka | Kľúčové stĺpce |
|---|---|
| `aggregate_reports` | org, report_id, domain, date_begin/end, publikovaná politika (p, sp, np, pct, adkim, aspf, fo), raw XML |
| `aggregate_records` | source_ip, count, disposition, dmarc_pass, spf_aligned, dkim_aligned, dkim_results a spf_results (JSON), PTR, sender, **category**, country. Denormalizované `domain`, `day` a `org_name` pre rýchle agregácie. Index `(domain, day)`. |
| `forensic_reports` | fingerprint, arrival_date, source_ip, reported_domain, auth_failure, predmet, polia feedback reportu (JSON), hlavičky |
| `tls_reports` | jeden riadok na politiku: policy_domain, typ, MX, úspešné a neúspešné relácie, failure_details (JSON) |
| `domains` | name, výsledok DNS kontroly (JSON), skóre, časy |

Tabuľky sa vytvárajú automaticky (`Base.metadata.create_all`).

## Bezpečnosť

| Oblasť | Riešenie |
|---|---|
| Session | podpísané cookie (`itsdangerous`), `SameSite=Lax`, `Secure` cez `COOKIE_SECURE`, absolútna expirácia `SESSION_MAX_AGE_HOURS` |
| CSRF | token v session, overenie pri každom POST (formulárové pole `csrf_token` alebo hlavička `X-CSRF-Token`) |
| Heslá | `scrypt` (N=2^14, r=8, p=1), 16 B soľ, porovnanie v konštantnom čase |
| Brute force | 8 neúspešných pokusov za 5 minút na kombináciu IP a meno |
| Údaje schránok | Fernet (AES-128-CBC + HMAC), kľúč = SHA-256 zo `SECRET_KEY`. Heslá sa nikdy nevracajú do formulára. |
| LDAP | escapovanie filtrov, overenie hesla bindom, LDAPS a StartTLS s overením certifikátu |
| OIDC | authorization code flow so `state` a `nonce` (authlib), whitelist skupín, ochrana proti kolízii účtov medzi zdrojmi |
| Vstupy | `defusedxml` (XXE, billion laughs), limity veľkosti archívov a príloh, JSON do `<script>` s escapovaním `<`, `>`, `&` |
| Redirect | parameter `next` po prihlásení prijíma iba relatívne cesty |
| Hlavičky | `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin` |
| Autorizácia | `viewer` iba číta, všetko pod `/admin` vyžaduje `admin` |

## HTTP rozhranie

| Metóda a cesta | Rola | Popis |
|---|---|---|
| `GET /healthz` | – | health check |
| `GET /login`, `POST /login`, `POST /logout` | – | lokálne a LDAP prihlásenie |
| `GET /auth/oidc/login`, `/auth/oidc/callback` | – | SSO |
| `GET /`, `/domains`, `/domains/{d}`, `/sources`, `/reports`, `/reports/{id}`, `/forensic`, `/forensic/{id}`, `/tls`, `/tools`, `/profile` | viewer | stránky (parametre `days`, `domain`, `start`, `end`) |
| `GET /sources.csv`, `/reports/{id}/xml` | viewer | exporty |
| `POST /domains/{d}/check` | viewer | DNS kontrola domény |
| `GET /api/dashboard?days=&domain=` | viewer (session) | JSON so súhrnom, časovou osou, kategóriami, službami a doménami |
| `/admin/sources*`, `/admin/upload`, `/admin/users*`, `/admin/dns-refresh`, `/admin/domains/{id}/delete` | admin | administrácia |

## Rozšírenie

- **Nová známa služba:** pridajte doménu do `KNOWN_SENDERS` v `app/analysis/senders.py` (kľúč je doména alebo suffix z PTR, DKIM `d=` alebo SPF). Existujúce záznamy sa nepreklasifikujú, zmena platí pre nové reporty.
- **Nový graf:** dopyt pridajte do `app/analysis/stats.py`, dáta odovzdajte šablóne a vykreslite helperom z `app/static/js/app.js` (`DM.volume`, `DM.donut`, `DM.hbar`…). Farby berte z CSS premenných (`--series-1..8`, `--good`, `--warning`, `--critical`), aby fungoval aj tmavý režim.
- **Nový typ zdroja:** implementujte triedu s metódami `test()`, `fetch(limit)` (vracia `(id, predmet, raw_bytes)`), `done(id, ok)` a `close()` a zaregistrujte ju v `open_mailbox()`.

## Vývoj a testy

```bash
pip install -r requirements.txt pytest ruff
pytest                                  # parser + celý tok (login, CSRF, upload, všetky stránky)
ruff check app tests
python -m app.demo --days 60            # demo dáta
uvicorn app.main:app --reload
```
