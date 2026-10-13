# ruff: noqa: I001  – the test DB must be configured before app modules are imported
import tests.test_app  # noqa: F401

import time  # noqa: E402

import dns.exception  # noqa: E402
import dns.resolver  # noqa: E402

from app.analysis import dns_checks  # noqa: E402
from app.db import init_db, session_scope  # noqa: E402
from app.ingest.parser import extract_from_file  # noqa: E402
from app.ingest.service import report_evidence, store_result  # noqa: E402
from tests.test_parser import AGG  # noqa: E402


class _Resolver:
    def __init__(self, exc):
        self.exc = exc

    def resolve(self, name, rdtype):
        raise self.exc()


def test_resolver_failure_is_unknown_not_missing(monkeypatch):
    monkeypatch.setattr(dns_checks, "_resolver", lambda: _Resolver(dns.exception.Timeout))
    r = dns_checks.check_domain("example.sk")
    assert r["dmarc"]["status"] == r["spf"]["status"] == r["dkim"]["status"] == "unknown"
    assert r["score"] is None and r["grade"] == "?"
    assert "DNS server neodpovedal" in r["dmarc"]["issues"][0]["msg"]


def test_nxdomain_is_still_missing(monkeypatch):
    monkeypatch.setattr(dns_checks, "_resolver", lambda: _Resolver(dns.resolver.NXDOMAIN))
    r = dns_checks.check_domain("example.sk")
    assert r["dmarc"]["status"] == "error" and r["dmarc"]["record"] is None
    assert r["score"] == 0


def test_reports_prove_record_hidden_by_split_horizon_dns(monkeypatch):
    init_db()
    now = int(time.time())
    xml = (AGG.replace(b"<report_id>123</report_id>", b"<report_id>split-1</report_id>")
           .replace(b"1760313600", str(now - 86400).encode()).replace(b"1760399999", str(now - 60).encode()))
    with session_scope() as db:
        store_result(db, extract_from_file("r.xml", xml), "test")
    # internal zone answers authoritatively "no TXT record"
    monkeypatch.setattr(dns_checks, "_resolver", lambda: _Resolver(dns.resolver.NoAnswer))
    with session_scope() as db:
        r = report_evidence(db, "example.sk", dns_checks.check_domain("example.sk"))
    assert r["dmarc"]["status"] == "unknown" and r["spf"]["status"] == "unknown"
    assert "split-horizon" in r["dmarc"]["issues"][0]["msg"] and r["grade"] == "?"
