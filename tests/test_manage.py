# ruff: noqa: I001  – the test DB must be configured before app modules are imported
import tests.test_app  # noqa: F401  (configures the test database before app imports)

from sqlalchemy import select  # noqa: E402

from app import manage  # noqa: E402
from app.db import init_db, session_scope  # noqa: E402
from app.ingest.parser import extract_from_file  # noqa: E402
from app.ingest.service import store_result  # noqa: E402
from app.models import AggregateRecord, AggregateReport, MailSource, User, utcnow  # noqa: E402
from app.security import verify_password  # noqa: E402
from tests.test_parser import AGG  # noqa: E402

# same report, but from 2020 and with a different id
OLD = (AGG.replace(b"<report_id>123</report_id>", b"<report_id>old-2020</report_id>")
       .replace(b"1760313600", b"1577836800").replace(b"1760399999", b"1577923199"))


def test_prune_dry_run_and_delete():
    init_db()
    with session_scope() as db:
        assert store_result(db, extract_from_file("old.xml", OLD), "test")["new"] == 1
        old_id = db.scalar(select(AggregateReport.id).where(AggregateReport.report_id == "old-2020"))
    dry = manage.prune(days=365, dry_run=True)
    assert dry["aggregate"] >= 1
    with session_scope() as db:
        assert db.get(AggregateReport, old_id) is not None
    manage.prune(days=365 * 3)
    with session_scope() as db:
        assert db.get(AggregateReport, old_id) is None
        assert not db.scalars(select(AggregateRecord).where(AggregateRecord.report_id == old_id)).all()


def test_status_exit_codes():
    init_db()
    with session_scope() as db:
        db.query(MailSource).delete()
        db.add(MailSource(name="exo", kind="graph", config_encrypted="x", last_run=utcnow(), last_status="ok"))
    assert manage.status(max_age=60) == 0
    with session_scope() as db:
        db.scalar(select(MailSource)).last_status = "error"
    assert manage.status(max_age=60) == 1


def test_set_password_and_create_admin():
    init_db()
    assert manage.set_password("ops-admin", "VeryLongPass1", create_admin=True) == 0
    assert manage.set_password("ops-admin", "AnotherPass22") == 0
    assert manage.set_password("ops-admin", "short") == 2
    assert manage.set_password("nobody", "VeryLongPass1") == 2
    with session_scope() as db:
        u = db.scalar(select(User).where(User.username == "ops-admin"))
        assert u.role == "admin" and verify_password("AnotherPass22", u.password_hash)
