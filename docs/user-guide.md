# User guide

The UI is available in **Slovak and English**. Switch the language with **SK / EN** in the top bar or on the sign-in page; the choice is remembered in the browser (cookie). The default for new visitors is set by `DEFAULT_LANGUAGE`. This guide uses the English UI terms and, where useful, gives the Slovak term in parentheses.

## Filters

Every overview has a **domain selector** (*Všetky domény* = all domains) and a **period**
(7 days, 30 days, 90 days, 1 year) in the top right. Periods are based on the report's begin
date. Aggregate reports usually cover one UTC day. The moon icon toggles dark mode.

## Dashboard (*Prehľad*)

| Element | Meaning |
|---|---|
| **DMARC súlad** (compliance) | Share of messages that passed DMARC (aligned SPF or aligned DKIM). The arrow shows the change in percentage points vs. the previous period of the same length. |
| **Správ celkom** (messages) | Total messages in aggregate reports, with the change in volume. |
| **SPF / DKIM zarovnané** (aligned) | Share of messages with aligned, passing SPF or DKIM. For `p=reject`, DKIM matters most because it survives forwarding. |
| **Hrozby / neznáme** (threats) | Messages from unknown sources that failed DMARC. Below the number is how many receivers quarantined or rejected. |
| **Objem pošty** (volume) | Daily volume split into passed, forwarded and failed. |
| **Miera súladu v čase** (compliance over time) | Daily compliance in %. The dashed line is the 98 % target. |
| **Klasifikácia prevádzky** (classification) | The four traffic categories below. |
| **Zarovnanie SPF / DKIM** (alignment) | Messages passing both, only one, or neither. |
| **Aplikovaná politika** (disposition) | What receivers actually did: `none`, `quarantine`, `reject`. |
| **Odporúčania** (recommendations) | Actionable items per domain: DNS errors, non-compliant services, readiness for a stricter policy. With many domains, the 10 most severe are shown, and the link opens the domain list filtered to errors. |
| **Nové zlyhávajúce zdroje** (new failing sources) | IPs that failed DMARC in the last 7 days and had never been seen before. That's either a new service someone started using, or spoofing. |

## Traffic categories

| Category (UI) | When | What to do |
|---|---|---|
| 🟢 **V súlade** (compliant) | DMARC passed. | Nothing. |
| 🔵 **Preposlané** (forwarded) | DMARC failed, but the message was forwarded: the receiver gave a reason such as `forwarded` or `mailing_list`, or a mailbox provider / security gateway delivered it with a valid (unaligned) DKIM signature. | Usually nothing. Legitimate senders with aligned DKIM survive forwarding under `p=reject`. |
| 🟡 **Nesúlad (legitímny zdroj)** (non-compliant legitimate source) | DMARC failed and the source is a **known service** (SendGrid, Mailchimp, Salesforce, …), or Microsoft 365 signing with the default `onmicrosoft.com` key. | **Fix it.** Enable DKIM with your own domain in that service, or set up a custom bounce / return-path domain (aligned SPF). |
| 🔴 **Hrozba / neznámy** (threat / unknown) | DMARC failed and the source isn't recognized. | Investigate. If it's legitimate (your own relay, a printer, a CRM), configure DKIM/SPF for it. Otherwise it's spoofing, which `p=reject` stops. |

Services are recognized from the source IP's reverse DNS (PTR), the DKIM `d=` domain and the
SPF domain, plus the ASN when GeoIP is configured.

## Domains (*Domény*)

Every domain for which at least one report has arrived is listed automatically. Admins can also **add domains manually** (*Add domains manually*, one per line or comma separated). Manually added domains are DNS-checked immediately and then daily with all other domains, even before their first report arrives, which is handy for parked domains. The page has:

- **Summary tiles:** how many domains publish `p=reject`, `p=quarantine`, `p=none` or no DMARC
  record, and how many need attention.
- **Search and filters** by name, policy, status (*Chyba* error / *Upozornenie* warning / OK),
  and "only with traffic". Useful with 100+ domains.
- **Stav** (status) column with the most important issue of each domain.

### Domain detail: DNS score (A–F)

| Check | Points | What is scored |
|---|---|---|
| DMARC | 40 | record exists (10); policy reject 22 / quarantine 14 / none 3; `rua=` (5); pct=100 with no errors (3) |
| SPF | 25 | record (10); `-all` 8 / `~all` 6; at most 10 DNS lookups (7) |
| DKIM | 20 | a valid key found (15); all keys ≥ 2048 bits (5, otherwise 2) |
| MTA-STS | 7 | valid policy covering all MX hosts (7); TXT record only (3) |
| TLS-RPT | 4 | record exists |
| BIMI | 4 | valid record with VMC (4); without VMC (2) |

Grades: **A** ≥ 90, **B** ≥ 75, **C** ≥ 60, **D** ≥ 40, **F** < 40.

What the checks look for:
- **DMARC:** multiple records, invalid tags, `pct` < 100, `sp=none`, inheritance from the
  organizational domain, and authorization records for external report destinations.
- **SPF:** recursive DNS lookup count (limit 10, otherwise *permerror*), void lookups (limit 2),
  `+all` / `?all`, deprecated `ptr`, multiple records, includes without SPF.
- **DKIM:** checks the selectors seen in reports plus common ones (`selector1`, `selector2`,
  `google`, `k1`, `s1`, …), and reports each key's type and length, revoked keys (empty `p=`)
  and test mode `t=y`.
- **MTA-STS:** downloads `https://mta-sts.<domain>/.well-known/mta-sts.txt` and checks the mode
  and that all MX hosts are covered.

**Znova skontrolovať DNS** (re-check DNS) runs the check immediately. All domains are re-checked
automatically every `DNS_REFRESH_HOURS`.

### Domain detail: policy readiness

Readiness is based on the **compliance of legitimate mail** = *compliant / (compliant +
non-compliant)*. Forwarded mail and threats are excluded, because the domain owner can't fix them.

| Current policy | Recommended next step when |
|---|---|
| `p=none` → `p=quarantine` | compliance ≥ 98 % and at least 14 days of data |
| `p=quarantine` → `p=reject` | compliance ≥ 99 % |

## Recommended DMARC rollout

1. **Monitor:** publish `p=none; rua=mailto:…` and collect reports for 2–4 weeks.
2. **Inventory:** in *Odosielatelia* (senders), go through *Nesúlad* and *Hrozba / neznámy*. For
   every legitimate service, enable DKIM with your own domain.
3. **Quarantine gradually:** `p=quarantine; pct=25`, then 50, then 100. Watch for new failures
   from legitimate services.
4. **Reject:** once compliance is ≥ 99 %, set `p=reject`. Don't forget `sp=reject` for subdomains.
5. **Maintain:** watch *new failing sources* and the recommendations. A new service without
   DKIM shows up right away.

With many domains, start with the **parked domains** (no outgoing mail). They can go straight
to `p=reject` together with a null SPF (`v=spf1 -all`) and a null MX (`0 .`).

The **DMARC record generator** under *Nástroje* (tools) builds the TXT record for you.

## Senders (*Odosielatelia*)

- **Tabs** filter by category.
- **The services chart** shows volume per recognized service.
- **Najčastejšie kombinácie zlyhaní** (most common failure combinations) shows which DKIM domain
  (`d=`) and SPF domain (MAIL FROM) failing mail used. For example, `mcsv.net (pass)` for
  Mailchimp means Mailchimp signs with its own domain, so you need to configure your own.
- **The IP table** can be searched (IP, PTR, service, domain) and exported to **CSV**.

## Reports

- **Agregované reporty (RUA):** aggregate report list with a filter by reporter. The detail
  page shows every record with DKIM/SPF results and policy override reasons, and lets you
  download the original XML.
- **Forenzné reporty (RUF):** headers of individual failed messages (subject, sender, IP). Few
  receivers send them, and neither Google nor Microsoft does. Headers may contain personal
  data, so restrict access accordingly.
- **TLS-RPT:** successful and failed TLS sessions for delivery to your MX hosts, with failure
  types (e.g. `certificate-expired`, `sts-policy-fetch-error`).

## Tools (*Nástroje*)

- **Domain check:** a one-off DNS check of any domain (including third-party ones). Nothing is stored.
- **DMARC record generator:** builds a TXT record from the chosen options.

## Manual import (*Import reportov*, admin only)

Accepts `.xml`, `.xml.gz`, `.zip`, `.json(.gz)` (TLS-RPT) and `.eml` (a whole e-mail with
reports, or a forensic report). Useful for importing historical reports. Duplicates are skipped.
