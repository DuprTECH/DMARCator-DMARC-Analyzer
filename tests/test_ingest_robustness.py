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
