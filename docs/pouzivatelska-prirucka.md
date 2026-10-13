# Používateľská príručka

## Filtre

Na všetkých prehľadoch nájdete vpravo hore **výber domény** a **obdobie** (7 dní, 30 dní, 90 dní, 1 rok). Filter sa prenáša aj medzi stránkami. Obdobie sa počíta podľa dátumu začiatku reportu, ktorý zvyčajne pokrýva 24 hodín (UTC).
Ikona mesiaca prepína tmavý režim. Voľba sa uloží v prehliadači.

## Prehľad (dashboard)

| Prvok | Význam |
|---|---|
| **DMARC súlad** | Podiel správ, ktoré prešli DMARC (zarovnaný SPF alebo DKIM). Šípka ukazuje zmenu v percentuálnych bodoch oproti predchádzajúcemu obdobiu rovnakej dĺžky. |
| **Správ celkom** | Súčet správ v agregovaných reportoch a zmena objemu v %. |
| **SPF / DKIM zarovnané** | Podiel správ so zarovnaným a úspešným SPF alebo DKIM. Pre `p=reject` je dôležité najmä DKIM, lebo prežije aj preposlanie. |
| **Hrozby / neznáme** | Správy z neznámych zdrojov, ktoré neprešli DMARC. Nižšie je počet správ, ktoré príjemcovia dali do karantény alebo odmietli. |
| **Objem pošty** | Denný objem rozdelený na *Prešlo DMARC*, *Preposlané* a *Zlyhalo*. |
| **Miera súladu v čase** | Denný súlad v %. Prerušovaná čiara je cieľ 98 %. |
| **Klasifikácia prevádzky** | Rozdelenie do štyroch kategórií (pozri nižšie). |
| **Zarovnanie SPF / DKIM** | Koľko správ prešlo oboma, iba jedným alebo žiadnym mechanizmom. |
| **Aplikovaná politika** | Čo s poštou reálne urobili príjemcovia (`none`, `quarantine`, `reject`). |
| **Odporúčania** | Konkrétne kroky pre jednotlivé domény (DNS chyby, nesúladné služby, pripravenosť na sprísnenie). |
| **Nové zlyhávajúce zdroje** | IP adresy, ktoré prvýkrát zlyhali DMARC za posledných 7 dní. Môže ísť o novú službu, ktorú niekto začal používať, alebo o spoofing. |

## Kategórie prevádzky

Každý záznam z reportu sa zaradí do jednej z kategórií:

| Kategória | Kedy | Čo robiť |
|---|---|---|
| 🟢 **V súlade** | DMARC prešiel. | Nič. |
| 🔵 **Preposlané** | DMARC neprešiel, ale ide o preposlanie: príjemca uviedol dôvod `forwarded`, `mailing_list` a pod., alebo správu doručil poštový poskytovateľ či bezpečnostná brána s platným (nezarovnaným) DKIM. | Zvyčajne nič. Tieto správy pri `p=reject` nezablokujú legitímnych odosielateľov, ak majú zarovnaný DKIM. |
| 🟡 **Nesúlad (legitímny zdroj)** | DMARC neprešiel a zdroj je **známa služba** (SendGrid, Mailchimp, Salesforce…) alebo M365 podpisujúci doménou `onmicrosoft.com`. | **Opraviť.** Najčastejšie treba v službe zapnúť DKIM s vlastnou doménou alebo nastaviť vlastnú bounce/return-path doménu (zarovnaný SPF). |
| 🔴 **Hrozba / neznámy** | DMARC neprešiel a zdroj nie je rozpoznaný. | Overiť. Ak ide o legitímny server (napr. vlastný SMTP relay, tlačiareň, CRM), nastavte preň DKIM/SPF. Inak ide o spoofing a vyrieši ho `p=reject`. |

Služby sa rozpoznávajú podľa reverzného DNS (PTR) IP adresy, DKIM domény, SPF domény a pri GeoIP aj podľa ASN.

## Domény

Zoznam obsahuje všetky domény, pre ktoré prišiel aspoň jeden report. Detail domény obsahuje:

### DNS skóre (A–F)

| Kontrola | Body | Hodnotí sa |
|---|---|---|
| DMARC | 40 | záznam existuje (10), politika reject 22 / quarantine 14 / none 3, `rua=` (5), pct=100 bez chýb (3) |
| SPF | 25 | záznam (10), `-all` 8 / `~all` 6, max. 10 DNS lookupov (7) |
| DKIM | 20 | nájdený platný kľúč (15), všetky kľúče ≥ 2048 bitov (5, inak 2) |
| MTA-STS | 7 | platná politika pokrývajúca všetky MX (7), iba TXT záznam (3) |
| TLS-RPT | 4 | záznam existuje |
| BIMI | 4 | platný záznam s VMC (4), bez VMC (2) |

Známky: **A** ≥ 90, **B** ≥ 75, **C** ≥ 60, **D** ≥ 40, **F** < 40.

Čo jednotlivé kontroly sledujú:
- **DMARC**: viacnásobné záznamy, neplatné tagy, `pct` < 100, `sp=none`, dedenie z organizačnej domény, autorizácia externých príjemcov reportov.
- **SPF**: rekurzívny počet DNS lookupov (limit 10, inak *permerror*), void lookupy (limit 2), `+all` a `?all`, zastaraný `ptr`, viacnásobné záznamy, includy bez SPF.
- **DKIM**: overujú sa selektory videné v reportoch a bežné selektory (`selector1`, `selector2`, `google`, `k1`, `s1`…). Pri každom kľúči sa zisťuje typ a dĺžka, zrušené kľúče (prázdne `p=`) a testovací režim `t=y`.
- **MTA-STS**: stiahne sa politika z `https://mta-sts.<domena>/.well-known/mta-sts.txt` a overí sa režim a pokrytie všetkých MX.

Tlačidlo **Znova skontrolovať DNS** spustí kontrolu hneď. Automaticky prebieha raz za `DNS_REFRESH_HOURS`.

### Pripravenosť na vynútenie politiky

Pripravenosť sa počíta zo **súladu legitímnej pošty** = *V súlade / (V súlade + Nesúlad)*. Preposlané správy a hrozby sa do výpočtu nezarátavajú, lebo ich vlastník domény neovplyvní.

| Aktuálna politika | Podmienka odporúčania |
|---|---|
| `p=none` → `p=quarantine` | súlad ≥ 98 % a aspoň 14 dní s dátami |
| `p=quarantine` → `p=reject` | súlad ≥ 99 % |

## Odporúčaný postup zavedenia DMARC

1. **Monitoring:** `p=none; rua=mailto:…` a 2 až 4 týždne zbierajte reporty.
2. **Inventúra:** v *Odosielatelia* prejdite kategóriu *Nesúlad* a *Hrozba / neznámy*. Pre každú legitímnu službu nastavte DKIM s vlastnou doménou.
3. **Karanténa postupne:** `p=quarantine; pct=25`, potom `50` a `100`. Sledujte, či nepribúdajú zlyhania legitímnych služieb.
4. **Reject:** keď je súlad ≥ 99 %, nastavte `p=reject`. Nezabudnite na `sp=reject` pre subdomény.
5. **Údržba:** sledujte *Nové zlyhávajúce zdroje* a odporúčania na dashboarde. Nová služba bez DKIM sa okamžite ukáže.

Pomôcť vám pri tom môže **generátor DMARC záznamu** v časti *Nástroje*.

## Odosielatelia

- **Záložky** filtrujú podľa kategórie.
- **Graf služieb** ukazuje objem pošty podľa rozpoznanej služby.
- **Najčastejšie kombinácie zlyhaní** ukazujú, akú DKIM doménu (`d=`) a SPF doménu (MAIL FROM) mala zlyhávajúca pošta. Napr. `mcsv.net (pass)` pri Mailchimp znamená, že Mailchimp podpisuje vlastnou doménou a treba nastaviť vlastnú.
- **Tabuľka IP adries** sa dá vyhľadávať (IP, PTR, služba, doména) a exportovať do **CSV**.

## Reporty

- **Agregované reporty (RUA):** zoznam reportov s možnosťou filtrovať podľa reportéra. V detaile sú všetky záznamy s výsledkami DKIM/SPF, dôvodmi zmeny politiky (override) a pôvodným XML na stiahnutie.
- **Forenzné reporty (RUF):** kópie hlavičiek jednotlivých zlyhaných správ (predmet, odosielateľ, IP). Posiela ich len málo poskytovateľov, Google ani Microsoft nie. Hlavičky môžu obsahovať osobné údaje, s prístupom podľa toho narábajte.
- **TLS-RPT:** úspešné a zlyhané TLS relácie pri doručovaní k vašim MX a typy zlyhaní (napr. `certificate-expired`, `sts-policy-fetch-error`).

## Nástroje

- **Kontrola domény:** jednorazová DNS kontrola akejkoľvek domény (aj cudzej) bez uloženia výsledku.
- **Generátor DMARC záznamu:** zostaví TXT záznam podľa zvolených parametrov.

## Ručný import

*Administrácia → Import reportov* prijme súbory `.xml`, `.xml.gz`, `.zip`, `.json(.gz)` (TLS-RPT) a `.eml` (celý e-mail s reportom alebo forenzný report). Hodí sa na import historických reportov. Duplicitné reporty sa preskočia.
