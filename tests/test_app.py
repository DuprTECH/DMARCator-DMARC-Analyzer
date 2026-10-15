import os
import re
import tempfile

# TEST_DATABASE_URL lets the suite run against an empty MariaDB / PostgreSQL database
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or (
    "sqlite:///" + os.path.join(tempfile.mkdtemp(), "test.db").replace("\\", "/"))
os.environ["ADMIN_PASSWORD"] = "Sup3rSecret!!"
os.environ["RESOLVE_PTR"] = "false"
os.environ["POLL_INTERVAL_MINUTES"] = "999"
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["DEFAULT_LANGUAGE"] = "sk"  # the suite checks Slovak texts; the test client sends no Accept-Language

import gzip  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

import app.routes.admin as admin_routes  # noqa: E402
from app.main import app  # noqa: E402
from tests.test_parser import AGG, ARF, TLS  # noqa: E402

# Uploads and added domains start a DNS check in a background thread. In tests it would run real DNS
# lookups and write to the shared database concurrently with the next test – keep the suite deterministic.
admin_routes.refresh_dns = lambda **kwargs: None


def csrf(html: str) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def login(client):
    page = client.get("/login")
    r = client.post("/login", data={"username": "admin", "password": "Sup3rSecret!!", "csrf_token": csrf(page.text)},
                    follow_redirects=False)
    assert r.status_code == 303


def test_full_flow():
    with TestClient(app) as client:
        assert client.get("/", follow_redirects=False).headers["location"] == "/login"
        page = client.get("/login")
        bad = client.post("/login", data={"username": "admin", "password": "nope", "csrf_token": csrf(page.text)})
        assert bad.status_code == 401
        no_csrf = client.post("/login", data={"username": "admin", "password": "Sup3rSecret!!"})
        assert no_csrf.status_code == 403
        login(client)

        up = client.get("/admin/upload")
        files = [("files", ("a.xml.gz", gzip.compress(AGG), "application/gzip")),
                 ("files", ("t.json", TLS, "application/json")),
                 ("files", ("f.eml", ARF, "message/rfc822"))]
        r = client.post("/admin/upload", data={"csrf_token": csrf(up.text)}, files=files)
        assert "Nových reportov: 3" in r.text, r.text[:2000]
        r = client.post("/admin/upload", data={"csrf_token": csrf(up.text)}, files=files[:1])
        assert "duplicít: 1" in r.text

        for url in ["/?days=3650", "/domains?days=3650", "/domains/example.sk?days=3650", "/sources?days=3650",
                    "/sources?days=3650&category=threat", "/reports?days=3650", "/forensic?days=3650", "/tls?days=3650",
                    "/tools", "/admin/sources", "/admin/users", "/admin/sources/new?kind=graph",
                    "/admin/sources/new?kind=imap", "/profile", "/reports/1", "/forensic/1", "/sources.csv?days=3650"]:
            resp = client.get(url)
            assert resp.status_code == 200, (url, resp.text[:3000])
        dash = client.get("/?days=3650").text
        assert "example.sk" in dash
        api = client.get("/api/dashboard?days=3650").json()
        assert api["summary"]["total"] == 15 and api["summary"]["pass"] == 12
        assert client.get("/nope").status_code == 404

        # mailbox source CRUD (secret stays encrypted / preserved)
        page = client.get("/admin/sources/new?kind=graph")
        r = client.post("/admin/sources/save", data={
            "csrf_token": csrf(page.text), "kind": "graph", "name": "EXO", "enabled": "on", "tenant_id": "t",
            "client_id": "c", "client_secret": "s3cr3t", "mailbox": "dmarc@example.sk", "folder": "Inbox"},
            follow_redirects=False)
        assert r.status_code == 303
        edit = client.get("/admin/sources/1").text
        assert "s3cr3t" not in edit and "uložené" in edit
        assert 'name="sync_domains"' in edit and 'name="sync_domains" checked' not in edit


def test_mailbox_retention_form():
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import MailSource
    from app.security import decrypt_json

    with TestClient(app) as client:
        login(client)
        page = client.get("/admin/sources/new?kind=graph")
        assert 'name="cleanup_enabled"' in page.text
        data = {
            "csrf_token": csrf(page.text), "kind": "graph", "name": "retention-form",
            "tenant_id": "t", "client_id": "c", "mailbox": "retention@example.sk",
            "folder": "Inbox", "processed_folder": "Inbox/processed", "failed_folder": "Inbox/error",
            "cleanup_enabled": "on", "cleanup_days": "30",
        }
        assert client.post("/admin/sources/save", data=data, follow_redirects=False).status_code == 303
        with session_scope() as db:
            src = db.scalar(select(MailSource).where(MailSource.name == "retention-form"))
            sid = src.id
            cfg = decrypt_json(src.config_encrypted)
            assert cfg["cleanup_enabled"] is True and cfg["cleanup_days"] == 30
        edit = client.get(f"/admin/sources/{sid}")
        assert 'name="cleanup_enabled" checked' in edit.text
        data["id"] = str(sid)
        for invalid in ({"cleanup_days": "0"}, {"processed_folder": "Inbox/error"}):
            client.post("/admin/sources/save", data={**data, **invalid})
            with session_scope() as db:
                assert decrypt_json(db.get(MailSource, sid).config_encrypted) == cfg
        del data["cleanup_enabled"]
        client.post("/admin/sources/save", data=data)
        with session_scope() as db:
            assert decrypt_json(db.get(MailSource, sid).config_encrypted)["cleanup_enabled"] is False
        assert 'name="cleanup_enabled"' not in client.get("/admin/sources/new?kind=imap").text


def test_domains_from_csv_and_m365(monkeypatch):
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Domain

    m365_export = ("Domain name,Status,Choose a default domain\n"
                   "csv-one.sk,Healthy,No\ncontoso.onmicrosoft.com,Healthy,No\n\"csv-two.cz\",Incomplete setup,No\n").encode()
    assert admin_routes.domains_from_csv(m365_export) == {"csv-one.sk", "csv-two.cz"}
    assert admin_routes.domains_from_csv("Doména;Poznámka\ncsv-three.eu;hotel\n".encode("cp1250")) == {"csv-three.eu"}
    assert admin_routes.domains_from_csv(b"csv-four.com\r\ncsv-five.com\r\n") == {"csv-four.com", "csv-five.com"}

    with TestClient(app) as client:
        login(client)
        token = csrf(client.get("/domains").text)
        r = client.post("/admin/domains/add", data={"csrf_token": token, "domains": "typed.sk"},
                        files={"file": ("domains.csv", m365_export, "text/csv")})
        assert r.status_code == 200 and "Pridané domény: 3" in r.text

        class FakeGraph:
            def tenant_domains(self):
                return ["tenant-a.sk", "tenant-b.com"]

        from app.models import MailSource

        with session_scope() as db:
            if not db.scalar(select(MailSource.id).where(MailSource.kind == "graph")):
                db.add(MailSource(name="m365", kind="graph", config_encrypted="x"))
        monkeypatch.setattr(admin_routes, "open_mailbox", lambda src: FakeGraph())
        r = client.post("/admin/domains/import-m365", data={"csrf_token": token})
        assert "Pridané domény: 2" in r.text
    with session_scope() as db:
        names = set(db.scalars(select(Domain.name)))
    assert {"typed.sk", "csv-one.sk", "csv-two.cz", "tenant-a.sk", "tenant-b.com"} <= names


def test_ignored_domain_is_not_checked_or_counted(monkeypatch):
    from sqlalchemy import select

    from app.analysis import stats
    from app.db import init_db, session_scope
    from app.ingest import service
    from app.models import Domain

    init_db()
    with session_scope() as db:
        for name in ("watched-x.sk", "parked-x.sk"):
            if not db.scalar(select(Domain.id).where(Domain.name == name)):
                db.add(Domain(name=name))
    with TestClient(app) as client:
        login(client)
        with session_scope() as db:
            did = db.scalar(select(Domain.id).where(Domain.name == "parked-x.sk"))
        page = client.get("/domains/parked-x.sk")
        r = client.post(f"/admin/domains/{did}/ignore", data={"csrf_token": csrf(page.text), "ignored": "1"})
        assert "sa už nekontroluje" in r.text and "Znova kontrolovať túto doménu" in r.text
        assert "nekontroluje sa" in client.get("/domains").text

    checked = []
    monkeypatch.setattr(service.dns_checks, "check_domain", lambda name, sel: checked.append(name) or None)
    monkeypatch.setattr(service, "sync_tenant_domains", lambda: 0)
    service.refresh_dns()
    assert "watched-x.sk" in checked and "parked-x.sk" not in checked
    with session_scope() as db:
        total = db.scalar(select(stats.func.count()).select_from(Domain).where(stats.not_ignored()))
        assert sum(stats.dns_posture(db)["grades"].values()) == total

    with TestClient(app) as client:
        login(client)
        page = client.get("/domains/parked-x.sk")
        client.post(f"/admin/domains/{did}/ignore", data={"csrf_token": csrf(page.text), "ignored": "0"})
    with session_scope() as db:
        assert db.get(Domain, did).ignored is False
