# ruff: noqa: I001  – the test DB must be configured before app modules are imported
import tests.test_app as base

from datetime import date, datetime, timedelta  # noqa: E402

import pytest  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app import alerts  # noqa: E402
from app.db import init_db, session_scope  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AggregateRecord, AggregateReport, Alert, AppSetting, Domain  # noqa: E402

TODAY = date(2026, 10, 15)


@pytest.fixture(autouse=True)
def _cleanup():
    """The suite shares one database – remove the synthetic traffic so other tests see their own totals."""
    yield
    from sqlalchemy import delete

    with session_scope() as db:
        ids = select(AggregateReport.id).where(AggregateReport.domain.like("%.example"))
        db.execute(delete(AggregateRecord).where(AggregateRecord.report_id.in_(ids)))
        db.execute(delete(AggregateReport).where(AggregateReport.domain.like("%.example")))
        db.execute(delete(Domain).where(Domain.name.like("%.example")))
        db.execute(delete(Alert))
        db.execute(delete(AppSetting).where(AppSetting.key == alerts.SETTINGS_KEY))


def _day(db, domain, day, passed, failed):
    rep = AggregateReport(org_name="google.com", report_id=f"{domain}-{day}", domain=domain,
                          date_begin=datetime.combine(day, datetime.min.time()),
                          date_end=datetime.combine(day, datetime.max.time()))
    db.add(rep)
    db.flush()
    for count, ok in ((passed, True), (failed, False)):
        if count:
            db.add(AggregateRecord(report_id=rep.id, domain=domain, day=day, org_name="google.com",
                                   source_ip="192.0.2.1" if ok else "203.0.113.9", count=count, dmarc_pass=ok,
                                   spf_aligned=ok, dkim_aligned=ok, category="compliant" if ok else "threat"))


def _seed(domain, spike_failed, spike_passed=900):
    with session_scope() as db:
        db.add(Domain(name=domain))
        for i in range(2, 31):
            _day(db, domain, TODAY - timedelta(days=i), 950, 5)
        _day(db, domain, TODAY - timedelta(days=1), spike_passed, spike_failed)


def test_failure_spike_and_compliance_drop_are_recorded_once():
    init_db()
    _seed("spike.example", 400, spike_passed=500)
    _seed("calm.example", 8)
    with session_scope() as db:
        assert alerts.traffic_anomalies(db, TODAY) >= 2
        assert alerts.traffic_anomalies(db, TODAY) == 0  # the same events are not recorded again
        kinds = {(a.kind, a.domain) for a in db.scalars(select(Alert))}
    assert ("fail_spike", "spike.example") in kinds and ("compliance_drop", "spike.example") in kinds
    assert not any(d == "calm.example" for _, d in kinds)


def test_ignored_domain_raises_no_alert():
    init_db()
    _seed("parked-spike.example", 500)
    with session_scope() as db:
        db.scalar(select(Domain).where(Domain.name == "parked-spike.example")).ignored = True
    with session_scope() as db:
        alerts.traffic_anomalies(db, TODAY)
        assert not db.scalar(select(Alert.id).where(Alert.domain == "parked-spike.example"))


def test_removed_dns_record():
    init_db()
    old = {"dmarc": {"record": "v=DMARC1; p=reject", "status": "ok"}, "spf": {"record": "v=spf1 -all", "status": "ok"},
           "dkim": {"selectors": [{"selector": "s1"}], "status": "ok"}}
    new = {"dmarc": {"record": None, "status": "error"}, "spf": {"record": None, "status": "unknown"},
           "dkim": {"selectors": [], "status": "error"}}
    with session_scope() as db:
        alerts.dns_regressions(db, "dns.example", old, new)
        msgs = sorted(db.scalars(select(Alert.message).where(Alert.domain == "dns.example")))
    assert msgs == ["dns.example: záznam DKIM zmizol z DNS", "dns.example: záznam DMARC zmizol z DNS"]  # SPF unknown


def test_settings_sending_and_monthly_report(monkeypatch):
    init_db()
    sent = []
    monkeypatch.setattr(alerts, "send", lambda cfg, subject, html: sent.append((subject, html)))
    with TestClient(app) as client:
        base.login(client)
        page = client.get("/admin/alerts")
        assert page.status_code == 200 and "Upozornenia e-mailom" in page.text
        r = client.post("/admin/alerts", data={"csrf_token": base.csrf(page.text), "enabled": "on", "instant": "on",
                                               "monthly": "on", "recipients": "it@example.com", "transport": "smtp",
                                               "smtp_host": "smtp.example.com", "smtp_port": "587",
                                               "smtp_security": "starttls", "smtp_password": "s3cret", "min_failures": "50"})
        assert "uložené" in r.text and "s3cret" not in r.text
    cfg = alerts.load()
    assert cfg["enabled"] and cfg["smtp_password"] == "s3cret" and alerts.recipients(cfg) == ["it@example.com"]

    with session_scope() as db:
        alerts.record(db, "test:pending", "mailbox", None, "Schránka test nefunguje: timeout")
    assert alerts.check_and_notify() >= 1
    assert "Schránka test nefunguje" in sent[-1][1]
    assert alerts.check_and_notify() == 0  # nothing new

    _seed("monthly.example", 5)
    with session_scope() as db:
        alerts.record(db, "test:september", "fail_spike", "monthly.example", "monthly.example: 400 zlyhaní DMARC za 2026-09-20 (bežne okolo 5 denne)")
        db.scalar(select(Alert).where(Alert.key == "test:september")).created_at = datetime(2026, 9, 20, 8, 0)
    assert alerts.send_monthly(TODAY - timedelta(days=20)) is True
    subject, html = sent[-1]
    assert "Mesačný DMARC report 2026-09" in subject
    assert "monthly.example" in html and "400 zlyhaní DMARC za 2026-09-20" in html  # domain table and the month's events
    assert "Schránka test nefunguje" not in html  # an October event is not in the September report
    assert alerts.send_monthly(TODAY - timedelta(days=20)) is False  # once per month
