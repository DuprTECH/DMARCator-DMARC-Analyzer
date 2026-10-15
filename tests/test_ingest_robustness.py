# ruff: noqa: I001  – the test DB must be configured before app modules are imported
import tests.test_app  # noqa: F401

import re  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

from email.mime.application import MIMEApplication  # noqa: E402
from email.mime.multipart import MIMEMultipart  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import init_db, session_scope  # noqa: E402
from app.ingest import service  # noqa: E402
from app.ingest.parser import extract_from_file  # noqa: E402
from app.models import AggregateReport, IngestLog, MailSource  # noqa: E402
from tests.test_parser import AGG  # noqa: E402


def _report(report_id: str) -> bytes:
    return AGG.replace(b"<report_id>123</report_id>", f"<report_id>{report_id}</report_id>".encode())


def test_missing_date_range_uses_file_name():
    xml = re.sub(rb"<date_range>.*?</date_range>", b"", _report("nodate"), flags=re.S)
    res = extract_from_file("dmarc-report-gopass.travel-1788645656.xml", xml)
    assert not res.errors
    d = res.reports[0].data
    assert d["date_begin"] == datetime.fromtimestamp(1788645656, tz=timezone.utc).replace(tzinfo=None)
    assert (d["date_end"] - d["date_begin"]).total_seconds() == 86399
    # without a time stamp in the name the last day is used
    assert not extract_from_file("report.xml", xml).errors


def test_failing_report_does_not_break_the_others(monkeypatch):
    init_db()
    real_store = service.store
    calls = {"n": 0}

    def flaky_store(db, parsed, source):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OperationalError("INSERT ...", {}, Exception("(2006, 'MySQL server has gone away')"))
        return real_store(db, parsed, source)

    monkeypatch.setattr(service, "store", flaky_store)
    result = extract_from_file("a.xml", _report("flaky-1"))
    result.reports += extract_from_file("b.xml", _report("flaky-2")).reports
    with session_scope() as db:
        stats = service.store_result(db, result, "test")
        assert stats["new"] == 1 and len(stats["errors"]) == 1
        assert "server has gone away" in stats["errors"][0] and "INSERT" not in stats["errors"][0]
        # the session is still usable after the failure
        assert db.scalar(select(AggregateReport.id).where(AggregateReport.report_id == "flaky-2"))


def test_bad_message_does_not_block_the_mailbox(monkeypatch):
    init_db()

    def mail(report_id):
        msg = MIMEMultipart()
        part = MIMEApplication(_report(report_id), "xml")
        part.add_header("Content-Disposition", "attachment", filename="r.xml")
        msg.attach(part)
        return msg.as_bytes()

    class FakeMailbox:
        done_calls: list = []

        def fetch(self, limit):
            yield "m1", "poison", b"boom"
            yield "m2", "good", mail("mailbox-ok")

        def done(self, mid, ok):
            self.done_calls.append((mid, ok))

        def close(self):
            pass

    real_extract = service.extract_from_email

    def extract(raw):
        if raw == b"boom":
            raise OperationalError("SELECT ...", {}, Exception("(2013, 'Lost connection')"))
        return real_extract(raw)

    monkeypatch.setattr(service, "open_mailbox", lambda src: FakeMailbox())
    monkeypatch.setattr(service, "extract_from_email", extract)
    monkeypatch.setattr(service, "refresh_dns", lambda **kw: None)
    with session_scope() as db:
        src = MailSource(name="fake", kind="graph", config_encrypted="x")
        db.add(src)
        db.flush()
        sid = src.id
    message = service.run_source(sid)
    assert "1 nových reportov" in message and "1 chýb" in message
    assert FakeMailbox.done_calls == [("m1", False), ("m2", True)]
    with session_scope() as db:
        logs = [m for m in db.scalars(select(IngestLog.message).where(IngestLog.source == "fake"))]
        assert any("'poison'" in m and "Lost connection" in m for m in logs)
        assert db.get(MailSource, sid).last_status == "warn"


def test_large_xml_is_not_stored_verbatim(monkeypatch):
    init_db()
    small = get_settings().model_copy(update={"raw_xml_max_kb": 0})
    monkeypatch.setattr(service, "get_settings", lambda: small)
    with session_scope() as db:
        service.store_result(db, extract_from_file("big.xml", _report("big-1")), "test")
        rep = db.scalar(select(AggregateReport).where(AggregateReport.report_id == "big-1"))
        assert rep.raw_xml is None and len(rep.records) == 2


ORIGINAL = b"""From: Gopass <info@gopass.travel>
To: someone@example.com
Subject: Planned maintenance
Date: Tue, 13 Oct 2026 10:00:00 +0000
Message-ID: <abc@gopass.travel>

body
"""


def _wrap(subject: str, parts: list) -> bytes:
    from email.mime.base import MIMEBase
    from email.mime.text import MIMEText

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = "dmarc@reporter.example"
    for kind, payload in parts:
        if kind == "text":
            msg.attach(MIMEText(payload, "plain"))
        else:
            part = MIMEBase(*kind.split("/"))
            part.set_payload(payload)
            msg.attach(part)
    return msg.as_bytes()


def test_forensic_variants():
    from app.ingest.parser import extract_from_email

    # 1) plain-text ARF fields, original message attached, non-standard container
    raw = _wrap("DMARC Forensic Report for gopass.travel from IP 212.77.101.8", [
        ("text", "This is a DMARC failure report.\nFeedback-Type: auth-failure\nSource-IP: 212.77.101.8\n"
                 "Reported-Domain: gopass.travel\nAuth-Failure: dmarc\n"),
        ("text/rfc822-headers", ORIGINAL.decode()),
    ])
    d = extract_from_email(raw).reports[0].data
    assert (d["source_ip"], d["reported_domain"], d["auth_failure"]) == ("212.77.101.8", "gopass.travel", "dmarc")
    assert d["subject"] == "Planned maintenance"

    # 2) only the subject identifies it
    raw = _wrap("DMARC Forensic Report for gopass.travel from IP 209.85.222.175", [
        ("text", "A message failed DMARC."), ("text/rfc822-headers", ORIGINAL.decode())])
    d = extract_from_email(raw).reports[0].data
    assert d["source_ip"] == "209.85.222.175" and d["reported_domain"] == "gopass.travel"

    # 3) Source-IP holding several values / text -> first valid address
    raw = _wrap("x", [("message/feedback-report",
                       "Feedback-Type: auth-failure\nSource-IP: 209.85.208.41, 209.85.208.42 (mail-ed1-f41.google.com)\n"
                       "Reported-Domain: gopass.travel\n")])
    d = extract_from_email(raw).reports[0].data
    assert d["source_ip"] == "209.85.208.41"

    # 4) an ordinary notification is not mistaken for a report
    raw = _wrap("Centrum zabezpečenia pre Microsoft 365: Máte správy v karanténe", [("text", "Hello")])
    res = extract_from_email(raw)
    assert not res.reports and not res.errors


def test_values_are_fitted_to_columns():
    init_db()
    long_from = ("x" * 300) + ".example.sk"
    xml = _report("long-1").replace(b"<header_from>example.sk</header_from>", f"<header_from>{long_from}</header_from>".encode(), 1)
    with session_scope() as db:
        stats = service.store_result(db, extract_from_file("r.xml", xml), "test")
        assert stats["new"] == 1 and not stats["errors"]
        rep = db.scalar(select(AggregateReport).where(AggregateReport.report_id == "long-1"))
        assert max(len(r.header_from) for r in rep.records) <= 255


def test_invalid_messages_and_failed_reports_use_failed_folder(monkeypatch):
    init_db()

    class FakeMailbox:
        done_calls = []

        def fetch(self, limit):
            yield "empty", "notification", _wrap("notification", [("text", "Hello")])
            yield "bad", "report", _wrap("report", [("application/xml", _report("routing-failure").decode())])

        def done(self, mid, ok):
            self.done_calls.append((mid, ok))

        def close(self):
            pass

    def fail_store(*args):
        raise OperationalError("INSERT", {}, Exception("storage failed"))

    monkeypatch.setattr(service, "store", fail_store)
    monkeypatch.setattr(service, "open_mailbox", lambda src: FakeMailbox())
    with session_scope() as db:
        src = MailSource(name="routing-test", kind="graph", config_encrypted="x")
        db.add(src)
        db.flush()
        sid = src.id
    assert "1 chýb" in service.run_source(sid)
    assert FakeMailbox.done_calls == [("empty", False), ("bad", False)]


def test_cleanup_runs_without_new_messages_and_logs_failure(monkeypatch):
    from app.ingest.graph import GraphError, GraphMailbox

    init_db()
    box = GraphMailbox.__new__(GraphMailbox)
    box.cfg = {"cleanup_enabled": True}
    box.fetch = lambda limit: iter(())
    box.close = lambda: None
    box.cleanup = lambda: 7
    monkeypatch.setattr(service, "open_mailbox", lambda src: box)
    with session_scope() as db:
        src = MailSource(name="cleanup-test", kind="graph", config_encrypted="x")
        db.add(src)
        db.flush()
        sid = src.id
    service.run_source(sid)
    with session_scope() as db:
        assert db.get(MailSource, sid).last_status == "ok"
        assert any("7 spracovaných" in message for message in db.scalars(
            select(IngestLog.message).where(IngestLog.source == "cleanup-test")))

    def fail_cleanup():
        raise GraphError("cleanup permission denied")

    box.cleanup = fail_cleanup
    assert "cleanup permission denied" in service.run_source(sid)
    with session_scope() as db:
        assert db.get(MailSource, sid).last_status == "error"
