# ruff: noqa: I001  – the test DB must be configured before app modules are imported
import tests.test_app as base  # noqa: F401

import glob  # noqa: E402
import gzip  # noqa: E402
import re  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app import i18n  # noqa: E402
from app.analysis import dns_checks  # noqa: E402
from app.analysis.senders import CATEGORIES  # noqa: E402
from app.i18n_en import EN  # noqa: E402
from app.main import app  # noqa: E402
from tests.test_parser import AGG, ARF, TLS  # noqa: E402

SLOVAK = re.compile(r"[áäčďéíĺľňóôŕšťúýžÁČĎÉÍĽŇÓŠŤÚÝŽ]")
CALL = re.compile(r"""_\(\s*(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")""")


def test_every_template_string_has_english_translation():
    strings = set(CATEGORIES.values()) | set(i18n.JS_STRINGS)
    for f in glob.glob("app/templates/**/*.html", recursive=True):
        for m in CALL.finditer(open(f, encoding="utf-8").read()):
            strings.add(m.group(1) if m.group(1) is not None else m.group(2))
    missing = sorted(s for s in strings if s not in EN)
    assert not missing, missing


def _en(text):
    i18n.set_language("en")
    try:
        return i18n.translate(text)
    finally:
        i18n.set_language("sk")


def test_generated_dns_findings_translate(monkeypatch):
    """Run the DNS checks against crafted records so most finding messages are produced, then translate them."""
    records = {
        "_dmarc.bad.sk": ['v=DMARC1; p=none; pct=50; sp=none; rua=mailto:r@other.net', "v=DMARC1; p=none"],
        "bad.sk": ["v=spf1 ptr include:a.bad.sk include:missing.bad.sk ?all", "v=spf1 -all"],
        "a.bad.sk": ["v=spf1 " + " ".join(f"include:i{i}.bad.sk" for i in range(11)) + " ~all"],
        "selector1._domainkey.bad.sk": ["v=DKIM1; t=y; p="],
        "_mta-sts.bad.sk": ["v=STSv1; id=1"],
        "default._bimi.bad.sk": ["v=BIMI1; l=https://x/logo.svg"],
    }

    def fake_txt(name):
        if name in records:
            return records[name]
        if name.startswith("i") and name.endswith(".bad.sk"):
            return ["v=spf1 -all"]
        return None

    monkeypatch.setattr(dns_checks, "txt", fake_txt)
    monkeypatch.setattr(dns_checks, "check_mx", lambda d: {"hosts": [], "provider": None, "status": "info", "issues": [
        dns_checks._issue("info", "Doména nemá MX – neprijíma poštu (zvážte null MX a p=reject).")]})
    r = dns_checks.check_domain("bad.sk", ["s9"])
    messages = [i["msg"] for k in ("dmarc", "spf", "dkim", "mx", "mta_sts", "tls_rpt", "bimi") for i in r[k]["issues"]]
    assert len(messages) > 12
    untranslated = [m for m in messages if SLOVAK.search(_en(m))]
    assert not untranslated, untranslated


def test_pages_in_english_contain_no_slovak():
    with TestClient(app) as client:
        base.login(client)
        page = client.get("/admin/upload")
        files = [("files", ("a.xml.gz", gzip.compress(AGG), "application/gzip")),
                 ("files", ("t.json", TLS, "application/json")), ("files", ("f.eml", ARF, "message/rfc822")),
                 ("files", ("junk.txt", b"hello", "text/plain"))]
        client.post("/admin/upload", data={"csrf_token": base.csrf(page.text)}, files=files)
        page = client.get("/domains")
        client.post("/admin/domains/add", data={"csrf_token": base.csrf(page.text), "domains": "example.org, not a domain"})
        client.cookies.set("lang", "en")
        urls = ["/?days=3650", "/domains?days=3650", "/domains/example.sk?days=3650", "/sources?days=3650",
                "/sources?days=3650&category=threat", "/reports?days=3650", "/reports/1", "/forensic?days=3650",
                "/forensic/1", "/tls?days=3650", "/tools", "/admin/sources", "/admin/users", "/admin/upload", "/admin/auth",
                "/admin/sources/new?kind=graph", "/admin/sources/new?kind=imap", "/profile", "/nope"]
        for url in urls:
            html = client.get(url).text
            assert '<html lang="en">' in html, url
            visible = re.sub(r"<script.*?</script>|<pre>.*?</pre>|<option[^>]*>Slovenčina</option>|title=\"Slovenčina\"",
                             "", html, flags=re.S)
            visible = re.sub(r"<[^>]+>", " ", visible)
            bad = SLOVAK.findall(visible)
            assert not bad, (url, re.findall(r".{40}[áäčďéíĺľňóôŕšťúýžÁČĎÉÍĽŇÓŠŤÚÝŽ].{40}", visible, re.S)[:5])
        # flash message after a POST is translated too
        page = client.get("/domains")
        r = client.post("/admin/domains/add", data={"csrf_token": base.csrf(page.text), "domains": "example.net"})
        assert "Domains added: 1" in r.text
        client.cookies.set("lang", "sk")
        assert '<html lang="sk">' in client.get("/").text


def test_language_switch_cookie():
    with TestClient(app) as client:
        r = client.get("/lang/en", headers={"referer": "http://testserver/login"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login"
        assert "lang=en" in r.headers["set-cookie"]
        r = client.get("/lang/en", headers={"referer": "https://evil.example/x"}, follow_redirects=False)
        assert r.headers["location"] == "/"
        client.cookies.set("lang", "en")
        assert "Sign in" in client.get("/login").text


def test_browser_language_detection():
    assert i18n.browser_language("sk-SK,sk;q=0.9,en;q=0.8") == "sk"
    assert i18n.browser_language("sk") == "sk"
    assert i18n.browser_language("en-US,en;q=0.9,sk;q=0.8") == "en"
    assert i18n.browser_language("cs-CZ,cs;q=0.9") == "en"
    assert i18n.browser_language("de;q=0.5, sk;q=0.9") == "sk"
    assert i18n.browser_language(None) == "en"
    assert i18n.browser_language("", "sk") == "sk"
    with TestClient(app) as client:
        assert '<html lang="sk">' in client.get("/login", headers={"accept-language": "sk-SK,sk;q=0.9"}).text
        assert '<html lang="en">' in client.get("/login", headers={"accept-language": "de-DE,de"}).text
        client.cookies.set("lang", "sk")   # an explicit choice wins over the browser
        assert '<html lang="sk">' in client.get("/login", headers={"accept-language": "en-US"}).text
