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
