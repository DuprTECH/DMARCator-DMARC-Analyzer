# Inštalácia a prevádzka

## Požiadavky

- Docker a Docker Compose **alebo** Python 3.11+.
- Odchádzajúci prístup na `login.microsoftonline.com` a `graph.microsoft.com` (alebo na IMAP server) a na DNS (port 53).
- Pre MTA-STS kontrolu odchádzajúce HTTPS na `mta-sts.<domena>`.
- Prehliadač používateľov potrebuje prístup na `cdn.jsdelivr.net` (ECharts a mapa sveta). V izolovanej sieti pozri [Offline prostredie](#offline-prostredie).

## Docker Compose (odporúčané)

Compose spustí aplikáciu a PostgreSQL 16.

```bash
git clone https://github.com/DuprTECH/DMARCula---DMARC-Anylzer.git dmarcula
cd dmarcula
cp .env.example .env
```

V `.env` nastavte aspoň tieto hodnoty:

```ini
SECRET_KEY=<výstup: python -c "import secrets;print(secrets.token_urlsafe(48))">
ADMIN_PASSWORD=<silné heslo prvého admina>
BASE_URL=https://dmarc.contoso.sk
COOKIE_SECURE=true
POSTGRES_PASSWORD=<heslo databázy>
```

Potom aplikáciu spustite:

```bash
docker compose up -d --build
docker compose logs -f dmarcula
```

Aplikácia počúva na porte `8000`. Stav overíte cez `GET /healthz`, ktorý vráti `{"status":"ok"}`.

> `ADMIN_PASSWORD` sa použije **iba pri prvom štarte**, keď je tabuľka používateľov prázdna. Neskôr sa heslá menia v časti *Administrácia → Používatelia*.

## Lokálna inštalácia (SQLite)

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # SECRET_KEY, ADMIN_PASSWORD
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Databáza sa vytvorí v `./data/dmarcula.db`. Ukážkové dáta (3 domény, 60 dní) nahráte príkazom `python -m app.demo --days 60`.

### Windows služba

Na Windows serveri je najjednoduchšie spustiť `uvicorn` cez [NSSM](https://nssm.cc/):

```powershell
nssm install DMARCula "C:\dmarcula\.venv\Scripts\python.exe" "-m uvicorn app.main:app --port 8000"
nssm set DMARCula AppDirectory C:\dmarcula
nssm start DMARCula
```

### Linux (systemd)

```ini
# /etc/systemd/system/dmarcula.service
[Unit]
Description=DMARCula
After=network-online.target

[Service]
User=dmarcula
WorkingDirectory=/opt/dmarcula
EnvironmentFile=/opt/dmarcula/.env
ExecStart=/opt/dmarcula/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers
Restart=always

[Install]
WantedBy=multi-user.target
```

## Dôležité: iba jeden proces

Plánovač, ktorý sťahuje reporty (každých `POLL_INTERVAL_MINUTES`) a obnovuje DNS kontroly (každých `DNS_REFRESH_HOURS`), beží **vo vnútri aplikácie**. Nespúšťajte preto viac workerov (`--workers N`) ani viac replík. Schránky by sa spracovávali súbežne. Jeden proces bez problémov zvládne desiatky domén a milióny záznamov.

## Reverzný proxy a HTTPS

Aplikácia má bežať za reverzným proxy s TLS. Nastavte `COOKIE_SECURE=true` a `BASE_URL` na verejnú HTTPS adresu, inak nebude fungovať SSO redirect.

**nginx:**

```nginx
server {
    listen 443 ssl http2;
    server_name dmarc.contoso.sk;
    ssl_certificate     /etc/ssl/dmarc.crt;
    ssl_certificate_key /etc/ssl/dmarc.key;
    client_max_body_size 60m;            # ručný upload reportov

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

**IIS** (ARR + URL Rewrite): vytvorte reverse proxy pravidlo na `http://localhost:8000` a povoľte serverovú premennú `HTTP_X_FORWARDED_PROTO`.

## Zálohovanie

| Databáza | Záloha |
|---|---|
| PostgreSQL | `docker compose exec db pg_dump -U dmarcula dmarcula > dmarcula-$(date +%F).sql` |
| SQLite | `sqlite3 data/dmarcula.db ".backup dmarcula-backup.db"` (bezpečné aj za behu) |

Zálohujte aj súbor **`.env`**. Prístupové údaje ku schránkam sú v databáze zašifrované kľúčom odvodeným zo `SECRET_KEY`. Bez neho ich neobnovíte a treba ich zadať znova.

## Aktualizácia

```bash
git pull
docker compose up -d --build
```

Nové tabuľky sa vytvoria automaticky pri štarte. Pri zmene existujúcich stĺpcov bude postup popísaný v poznámkach k vydaniu.

## Offline prostredie

1. Stiahnite `https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js` do `app/static/js/`.
2. V `app/templates/base.html` zmeňte `<script src=…>` na `/static/js/echarts.min.js`.
3. Mapa sveta sa bez internetu nezobrazí. Graf krajín ako stĺpcový graf funguje ďalej.

## Riešenie problémov

| Príznak | Príčina / riešenie |
|---|---|
| Aplikácia nenaštartuje: `SECRET_KEY must be set` | Nastavte `SECRET_KEY` (aspoň 16 znakov). |
| Pri prvom štarte sa nedá prihlásiť | `ADMIN_PASSWORD` nebol nastavený pri prvom štarte. Nastavte ho, zastavte aplikáciu, vymažte DB (ak je prázdna) a spustite znova. |
| DNS kontroly trvajú dlho | Prvý DNS server neodpovedá. Nastavte `DNS_SERVERS=1.1.1.1,8.8.8.8` alebo interné resolvery. |
| Schránka: `Token: invalid_client` | Zlý alebo expirovaný client secret. |
| Schránka: `HTTP 403 ErrorAccessDenied` | Aplikácia nemá oprávnenie na schránku. Skontrolujte admin consent a RBAC scope ([Microsoft 365](microsoft-365.md)). |
| Schránka: `Priečinok 'X' neexistuje` | Cesta k priečinku sa zadáva od koreňa, napr. `Inbox/DMARC`. |
| SSO: `SSO prihlásenie zlyhalo` | Nesedí redirect URI. Musí byť presne `BASE_URL + /auth/oidc/callback`. |
| Nezobrazujú sa krajiny | Nie sú nastavené GeoLite2 databázy (`GEOIP_COUNTRY_DB`). |

Priebeh sťahovania a chyby jednotlivých správ nájdete v *Administrácia → Poštové schránky → Log spracovania* a v štandardnom výstupe aplikácie (`docker compose logs`).
