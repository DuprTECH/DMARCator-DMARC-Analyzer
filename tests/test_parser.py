import gzip
import io
import zipfile
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart

from app.analysis.senders import categorize, identify
from app.ingest.parser import extract_from_email, extract_from_file

AGG = b"""<?xml version="1.0"?>
<feedback xmlns="urn:ietf:params:xml:ns:dmarc-2.0">
  <report_metadata><org_name>google.com</org_name><email>noreply@google.com</email>
    <report_id>123</report_id><date_range><begin>1760313600</begin><end>1760399999</end></date_range></report_metadata>
  <policy_published><domain>Example.SK</domain><adkim>r</adkim><aspf>r</aspf><p>quarantine</p><sp>reject</sp><pct>100</pct></policy_published>
  <record><row><source_ip>40.107.22.5</source_ip><count>12</count>
    <policy_evaluated><disposition>none</disposition><dkim>pass</dkim><spf>fail</spf></policy_evaluated></row>
    <identifiers><header_from>example.sk</header_from></identifiers>
    <auth_results><dkim><domain>example.sk</domain><selector>selector1</selector><result>pass</result></dkim>
      <spf><domain>bounce.example.net</domain><result>pass</result></spf></auth_results></record>
  <record><row><source_ip>185.1.1.1</source_ip><count>3</count>
    <policy_evaluated><disposition>quarantine</disposition><dkim>fail</dkim><spf>fail</spf></policy_evaluated></row>
    <identifiers><header_from>example.sk</header_from></identifiers>
    <auth_results><spf><domain>evil.ru</domain><result>fail</result></spf></auth_results></record>
</feedback>"""

TLS = b"""{"organization-name":"Google Inc.","date-range":{"start-datetime":"2026-10-01T00:00:00Z","end-datetime":"2026-10-01T23:59:59Z"},
"contact-info":"x@google.com","report-id":"r1","policies":[{"policy":{"policy-type":"sts","policy-domain":"example.sk","mx-host":["mx.example.sk"]},
"summary":{"total-successful-session-count":10,"total-failure-session-count":2},"failure-details":[{"result-type":"starttls-not-supported","failed-session-count":2}]}]}"""

ARF = b"""From: dmarc@seznam.cz
Subject: report
MIME-Version: 1.0
Content-Type: multipart/report; report-type=feedback-report; boundary="b"

--b
Content-Type: text/plain

human
--b
Content-Type: message/feedback-report

Feedback-Type: auth-failure
Version: 1
Source-IP: 1.2.3.4
Reported-Domain: example.sk
Auth-Failure: dmarc
Arrival-Date: Mon, 12 Oct 2026 10:00:00 +0000

--b
Content-Type: text/rfc822-headers

From: Boss <boss@example.sk>
Subject: Urgent payment

--b--
"""


def test_aggregate_gzip_namespace():
    res = extract_from_file("r.xml.gz", gzip.compress(AGG))
    assert not res.errors
    rep = res.reports[0]
    assert rep.kind == "aggregate"
    d = rep.data
    assert d["domain"] == "example.sk" and d["p"] == "quarantine" and d["message_count"] == 15
    r0, r1 = d["records"]
    assert r0["dmarc_pass"] and r0["dkim_aligned"] and not r0["spf_aligned"]
    assert r0["dkim_results"][0]["selector"] == "selector1"
    assert not r1["dmarc_pass"] and r1["disposition"] == "quarantine"


def test_zip_inside_email():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("google.com!example.sk!1!2.xml", AGG)
    msg = MIMEMultipart()
    msg["Subject"] = "Report domain: example.sk"
    part = MIMEApplication(buf.getvalue(), "zip")
    part.add_header("Content-Disposition", "attachment", filename="report.zip")
    msg.attach(part)
    res = extract_from_email(msg.as_bytes())
    assert [r.kind for r in res.reports] == ["aggregate"]


def test_tls():
    res = extract_from_file("t.json.gz", gzip.compress(TLS))
    p = res.reports[0].data["policies"][0]
    assert res.reports[0].kind == "tls" and p["failure_count"] == 2 and p["policy_domain"] == "example.sk"


def test_forensic():
    res = extract_from_email(ARF)
    d = res.reports[0].data
    assert res.reports[0].kind == "forensic"
    assert d["source_ip"] == "1.2.3.4" and d["reported_domain"] == "example.sk"
    assert d["subject"] == "Urgent payment" and d["arrival_date"].year == 2026


def test_garbage():
    res = extract_from_file("x.txt", b"hello")
    assert not res.reports and res.errors


def test_sender_identification_and_category():
    assert identify("mail-db8eur05on2100.outbound.protection.outlook.com", [], []) == "Microsoft 365"
    assert identify(None, ["sendgrid.net"], []) == "SendGrid"
    base = {"dmarc_pass": False, "reasons": [], "dkim_results": [], "spf_results": []}
    assert categorize({**base, "dmarc_pass": True}, None) == "compliant"
    assert categorize(base, None) == "threat"
    assert categorize(base, "SendGrid") == "noncompliant"
    assert categorize({**base, "dkim_results": [{"domain": "x.com", "result": "pass"}]}, "Google Workspace") == "forwarded"
    assert categorize({**base, "dkim_results": [{"domain": "c.onmicrosoft.com", "result": "pass"}]}, "Microsoft 365") == "noncompliant"


def test_long_periods_are_weekly():
    import tests.test_app  # noqa: F401  configures the test database
    from app.analysis import stats
    from app.db import SessionLocal

    with SessionLocal() as db:
        year = stats.timeline(db, stats.Filter.from_params(365))
        month = stats.timeline(db, stats.Filter.from_params(30))
    assert 52 <= len(year) <= 54 and all(w["bucket"] == "week" for w in year)
    assert len(month) == 30 and "bucket" not in month[0]
