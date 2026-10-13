"""Seed realistic demo data:  python -m app.demo [--days 60] [--domains 3]

Generates synthetic aggregate, forensic and TLS reports through the real parser + store
pipeline (no DNS lookups for PTR – PTR names are synthetic).
"""

import argparse
import gzip
import random
from datetime import datetime, timedelta, timezone

from .analysis import enrich
from .db import init_db, session_scope
from .ingest.parser import extract_from_email, extract_from_file
from .ingest.service import store_result

BASE_DOMAINS = {"contoso.sk": ("reject", 1.0), "fabrikam.sk": ("quarantine", 0.4), "tailspin.eu": ("none", 0.15)}


def build_domains(n: int, rnd: random.Random) -> dict[str, tuple[str, float]]:
    """The three showcase domains plus synthetic ones with a long-tail volume distribution."""
    domains = dict(list(BASE_DOMAINS.items())[:n])
    for i in range(len(domains), n):
        policy = rnd.choices(["none", "quarantine", "reject"], [5, 2, 3])[0]
        domains[f"firma{i:03d}.{rnd.choice(['sk', 'cz', 'eu', 'com'])}"] = (policy, min(1.0, rnd.lognormvariate(-3, 1.2)))
    return domains
SOURCES = [
    # ip, ptr, dkim domain, spf domain, aligned dkim, aligned spf, weight
    ("40.107.22.{}", "mail-db8eur05on{}.outbound.protection.outlook.com", "{d}", "{d}", True, True, 50),
    ("209.85.220.{}", "mail-sor-f{}.google.com", "{d}", "{d}", True, True, 8),
    ("167.89.10.{}", "o{}.ptr1234.sendgrid.net", "{d}", "sendgrid.net", True, False, 10),
    ("198.2.180.{}", "mail{}.atl31.mcdlv.net", "mcsv.net", "mcsv.net", False, False, 6),
    ("54.240.3.{}", "a3-{}.smtp-out.amazonses.com", "amazonses.com", "amazonses.com", False, False, 4),
    ("40.92.18.{}", "mail-oln040092018{}.outbound.protection.outlook.com", "contoso.onmicrosoft.com", "outlook.com", False, False, 2),
    ("185.12.64.{}", None, None, "unknown-host.ru", False, False, 2),
    ("91.215.85.{}", None, None, "{d}", False, False, 2),
]
REPORTERS = [("google.com", "noreply-dmarc-support@google.com"), ("Outlook.com", "dmarcreport@microsoft.com"),
             ("Yahoo", "dmarchelp@yahooinc.com"), ("Mail.Ru", "dmarc_support@corp.mail.ru"), ("seznam.cz", "dmarc@seznam.cz")]


def _xml(domain, policy, org, email, begin, end, rid, rows):
    recs = []
    for ip, count, dkim_d, dkim_res, spf_d, spf_res, pol_dkim, pol_spf, disp in rows:
        dkim_xml = f"<dkim><domain>{dkim_d}</domain><selector>selector1</selector><result>{dkim_res}</result></dkim>" if dkim_d else ""
        recs.append(f"""<record><row><source_ip>{ip}</source_ip><count>{count}</count><policy_evaluated>
<disposition>{disp}</disposition><dkim>{pol_dkim}</dkim><spf>{pol_spf}</spf></policy_evaluated></row>
<identifiers><header_from>{domain}</header_from><envelope_from>{spf_d}</envelope_from></identifiers>
<auth_results>{dkim_xml}<spf><domain>{spf_d}</domain><result>{spf_res}</result></spf></auth_results></record>""")
    return f"""<?xml version="1.0" encoding="UTF-8"?><feedback><report_metadata><org_name>{org}</org_name><email>{email}</email>
<report_id>{rid}</report_id><date_range><begin>{int(begin.timestamp())}</begin><end>{int(end.timestamp())}</end></date_range></report_metadata>
<policy_published><domain>{domain}</domain><adkim>r</adkim><aspf>r</aspf><p>{policy}</p><sp>{policy}</sp><pct>100</pct></policy_published>
{''.join(recs)}</feedback>""".encode()


def seed(days: int, n_domains: int = 3):
    init_db()
    rnd = random.Random(42)
    domains = build_domains(n_domains, rnd)
    ptrs: dict[str, str | None] = {}
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    with session_scope() as db:
        for day in range(days, 0, -1):
            begin = today - timedelta(days=day)
            end = begin + timedelta(days=1) - timedelta(seconds=1)
            for domain, (policy, scale) in domains.items():
                for org, email in rnd.sample(REPORTERS, 3 if scale > 0.1 else 1):
                    rows = []
                    for ip_t, ptr_t, dkim_t, spf_t, al_dkim, al_spf, weight in SOURCES:
                        if rnd.random() < 0.35:
                            continue
                        n = rnd.randint(1, 20)
                        ip = ip_t.format(n)
                        ptrs[ip] = ptr_t.format(n) if ptr_t else None
                        count = max(1, int(rnd.gauss(weight * 6 * scale, weight * scale)))
                        dkim_d = dkim_t.format(d=domain) if dkim_t else None
                        spf_d = spf_t.format(d=domain)
                        dkim_res = "pass" if dkim_d and (al_dkim or rnd.random() < 0.8) else ("fail" if dkim_d else "none")
                        spf_res = "pass" if (al_spf or rnd.random() < 0.6) else "fail"
                        pol_dkim = "pass" if al_dkim and dkim_res == "pass" else "fail"
                        pol_spf = "pass" if al_spf and spf_res == "pass" else "fail"
                        passed = "pass" in (pol_dkim, pol_spf)
                        disp = "none" if passed or policy == "none" else policy
                        rows.append((ip, count, dkim_d, dkim_res, spf_d, spf_res, pol_dkim, pol_spf, disp))
                    if rows:
                        xml = _xml(domain, policy, org, email, begin, end, f"{org}-{domain}-{day}", rows)
                        enrich._lookup = lambda ip: {"ip": ip, "ptr": ptrs.get(ip), "country": rnd.choice(
                            ["Ireland", "Netherlands", "United States", "Germany", "Slovakia"]) if ptrs.get(ip) else rnd.choice(
                            ["Russia", "China", "Brazil"]), "country_code": None, "asn": None, "asn_org": None}
                        store_result(db, extract_from_file("r.xml.gz", gzip.compress(xml)), "demo")
            # TLS-RPT
            tls = ('{"organization-name":"Google Inc.","date-range":{"start-datetime":"%s","end-datetime":"%s"},'
                   '"contact-info":"smtp-tls-reporting@google.com","report-id":"tls-%d","policies":[{"policy":'
                   '{"policy-type":"sts","policy-string":["version: STSv1","mode: enforce"],"policy-domain":"contoso.sk",'
                   '"mx-host":["contoso-sk.mail.protection.outlook.com"]},"summary":{"total-successful-session-count":%d,'
                   '"total-failure-session-count":%d},"failure-details":%s}]}') % (
                begin.isoformat() + "Z", end.isoformat() + "Z", day, rnd.randint(300, 900), (f := rnd.choice([0, 0, 0, 2, 5])),
                '[{"result-type":"certificate-expired","sending-mta-ip":"209.85.220.41","receiving-mx-hostname":"contoso-sk.mail.protection.outlook.com","failed-session-count":%d}]' % f if f else "[]")
            store_result(db, extract_from_file("tls.json", tls.encode()), "demo")
            db.commit()
            print(f"  {begin.date()} done", flush=True)
        # a forensic sample
        eml = f"""From: dmarc@seznam.cz
To: ruf@contoso.sk
Subject: Report Domain: contoso.sk
MIME-Version: 1.0
Content-Type: multipart/report; report-type=feedback-report; boundary="b1"

--b1
Content-Type: text/plain

This is an authentication failure report.
--b1
Content-Type: message/feedback-report

Feedback-Type: auth-failure
User-Agent: demo/1.0
Version: 1
Original-Mail-From: <billing@contoso.sk>
Original-Rcpt-To: <victim@example.sk>
Arrival-Date: {(today - timedelta(days=2)).strftime('%a, %d %b %Y %H:%M:%S +0000')}
Source-IP: 185.12.64.7
Reported-Domain: contoso.sk
Auth-Failure: dmarc
Delivery-Result: reject

--b1
Content-Type: text/rfc822-headers

From: "Contoso Billing" <billing@contoso.sk>
To: victim@example.sk
Subject: Neuhradená faktúra – urgentné
Date: {(today - timedelta(days=2)).strftime('%a, %d %b %Y %H:%M:%S +0000')}
Message-ID: <fake123@unknown-host.ru>

--b1--
""".encode()
        store_result(db, extract_from_email(eml), "demo")
        for domain, (policy, _) in domains.items():
            from .models import Domain

            dom = db.query(Domain).filter_by(name=domain).one_or_none()
            if not dom:
                continue
            dom.dns = _fake_dns(domain, policy)
            dom.dns_score = dom.dns["score"]
            dom.dns_checked_at = today
    print(f"Demo data for {days} days and {len(domains)} domains created.")


def _fake_dns(domain, policy):
    from .analysis.dns_checks import grade

    lookups = {"contoso.sk": 6, "fabrikam.sk": 11, "tailspin.eu": 4}.get(domain, 3 + len(domain) % 9)
    score = {"reject": 92, "quarantine": 71, "none": 44}[policy]
    rec = f"v=DMARC1; p={policy}; rua=mailto:dmarc@{domain}; fo=1"
    spf_issues = [{"level": "error", "msg": f"{lookups} DNS lookupov – limit je 10 (permerror)."}] if lookups > 10 else []
    return {
        "domain": domain, "score": score, "grade": grade(score),
        "dmarc": {"record": rec, "tags": {"v": "DMARC1", "p": policy, "rua": f"mailto:dmarc@{domain}", "fo": "1"}, "external_destinations": [],
                  "status": "ok" if policy == "reject" else "warn",
                  "issues": [] if policy == "reject" else [{"level": "warn", "msg": f"Politika p={policy} – zvážte sprísnenie."}]},
        "spf": {"record": "v=spf1 include:spf.protection.outlook.com include:sendgrid.net -all", "lookups": lookups,
                "includes": ["spf.protection.outlook.com", "sendgrid.net"], "all": "-all",
                "status": "error" if spf_issues else "ok", "issues": spf_issues},
        "dkim": {"selectors": [{"selector": "selector1", "record": "v=DKIM1; k=rsa; p=…", "key_type": "rsa", "bits": 2048,
                                "revoked": False, "from_reports": True, "testing": False}], "status": "ok", "issues": []},
        "mx": {"hosts": [{"pref": 0, "host": f"{domain.replace('.', '-')}.mail.protection.outlook.com"}], "provider": "Microsoft 365", "status": "ok", "issues": []},
        "mta_sts": {"record": "v=STSv1; id=20260101", "policy": {"mode": "enforce", "max_age": "604800", "mx": ["*.mail.protection.outlook.com"]},
                    "status": "ok", "issues": []} if domain == "contoso.sk" else
                   {"record": None, "policy": None, "status": "missing", "issues": [{"level": "info", "msg": "MTA-STS nie je nasadené."}]},
        "tls_rpt": {"record": "v=TLSRPTv1; rua=mailto:tlsrpt@contoso.sk", "status": "ok", "issues": []} if domain == "contoso.sk" else
                   {"record": None, "status": "missing", "issues": [{"level": "info", "msg": "TLS-RPT nie je nastavené."}]},
        "bimi": {"record": None, "status": "missing", "issues": [{"level": "info", "msg": "BIMI nie je nastavené."}]},
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--domains", type=int, default=3)
    args = ap.parse_args()
    seed(args.days, args.domains)
