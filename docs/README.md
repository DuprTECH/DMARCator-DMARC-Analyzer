# Dokumentácia DMARCula

| Dokument | Pre koho | Obsah |
|---|---|---|
| [Inštalácia a prevádzka](instalacia.md) | správca servera | Docker, lokálna inštalácia, reverzný proxy, zálohovanie, aktualizácia, riešenie problémov |
| [Konfigurácia](konfiguracia.md) | správca servera | všetky premenné prostredia s popisom a predvolenými hodnotami |
| [Prihlasovanie a roly](prihlasovanie.md) | správca identít | lokálne účty, LDAP / Active Directory, SSO cez Entra ID a iné OIDC, mapovanie rolí |
| [Microsoft 365 a príjem reportov](microsoft-365.md) | správca M365 / DNS | registrácia aplikácie, obmedzenie na jednu schránku, IMAP OAuth2, DNS záznamy |
| [Používateľská príručka](pouzivatelska-prirucka.md) | bezpečnostný / mailový tím | dashboard, kategórie prevádzky, DNS skóre, postup od p=none k p=reject |
| [Architektúra](architektura.md) | vývojár | komponenty, tok dát, dátový model, bezpečnosť, rozšírenie |

## Čo je DMARC v skratke

**DMARC** (RFC 7489) hovorí prijímajúcim serverom, čo robiť so správou, ktorá sa vydáva za vašu doménu, ale neprešla overením:

- **SPF** overuje, či IP adresa odosielateľa smie posielať poštu za doménu z obálky (MAIL FROM).
- **DKIM** overuje kryptografický podpis správy.
- **Zarovnanie (alignment)** znamená, že doména z SPF alebo DKIM sa zhoduje s doménou v hlavičke `From:`, ktorú vidí používateľ. DMARC prejde, ak je zarovnané a úspešné **aspoň jedno** z nich.
- **Politika** `p=none` iba monitoruje, `p=quarantine` posiela do spamu, `p=reject` správu odmietne.

Príjemcovia (Google, Microsoft, Yahoo…) posielajú denné **agregované reporty** na adresu z `rua=`. DMARCula ich sťahuje, vyhodnocuje a ukazuje, kto posiela poštu za vaše domény a či ste pripravení na prísnejšiu politiku.
