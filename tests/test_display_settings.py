# ruff: noqa: I001
import tests.test_app as base

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app import settings_store
from app.db import session_scope
from app.i18n import set_language
from app.main import app
from app.models import AppSetting, IngestLog
from app.web import _dt, display_zone


def test_timezone_conversion_and_dst():
    token = display_zone.set(ZoneInfo("Europe/Bratislava"))
    set_language("sk")
    try:
        assert _dt(datetime(2026, 10, 15, 8, 30)) == "15.10.2026 10:30"
        assert _dt(datetime(2026, 1, 15, 8, 30, tzinfo=timezone.utc)) == "15.01.2026 09:30"
        assert _dt("2026-03-29T00:30:00+00:00") == "29.03.2026 01:30"
        assert _dt("2026-03-29T01:30:00+00:00") == "29.03.2026 03:30"
        assert _dt("2026-10-25T00:30:00+00:00") == "25.10.2026 02:30"
        assert _dt("2026-10-25T02:30:00+00:00") == "25.10.2026 03:30"
        assert _dt(date(2026, 10, 15)) == "15.10.2026"
        assert _dt(None) == "–"
        set_language("en")
        assert _dt(datetime(2026, 10, 15, 23, 30)) == "2026-10-16 01:30"
        display_zone.set(ZoneInfo("America/New_York"))
        assert _dt("2026-10-15") == "2026-10-15"
    finally:
        display_zone.reset(token)
        set_language("sk")


def test_web_display_settings_and_language_priority():
    try:
        with TestClient(app) as client:
            base.login(client)
            page = client.get("/admin/display")
            assert page.status_code == 200
            data = {
                "csrf_token": base.csrf(page.text),
                "display_timezone": "Europe/Bratislava", "display_language": "en",
            }
            assert client.post("/admin/display", data={**data, "csrf_token": "bad"},
                               follow_redirects=False).status_code == 403
            assert client.post("/admin/display", data=data, follow_redirects=False).status_code == 303
            assert settings_store.effective_settings().display_timezone == "Europe/Bratislava"
            assert '<html lang="en">' in client.get("/login", headers={"accept-language": "sk"}).text
            client.get("/lang/sk")
            assert '<html lang="sk">' in client.get("/login").text
            client.get("/lang/auto")
            assert '<html lang="en">' in client.get("/login").text
            for changes in ({"display_timezone": "not-a-zone"}, {"display_language": "xx"}):
                client.post("/admin/display", data={**data, **changes})
                assert settings_store.effective_settings().display_timezone == "Europe/Bratislava"
                assert settings_store.effective_settings().display_language == "en"
            settings_store._reset_cache()
            assert settings_store.effective_settings().display_timezone == "Europe/Bratislava"
            settings_store.clear_auth()
            assert settings_store.effective_settings().display_language == "en"
            with session_scope() as db:
                db.add(IngestLog(source="timezone-test", message="timezone-marker",
                                 created_at=datetime(2026, 10, 15, 8, 30)))
            html = client.get("/admin/sources").text
            assert "2026-10-15 10:30" in html and "Europe/Bratislava" in html
            # Context-local zone must not leak into subsequent calls outside a request.
            assert display_zone.get().key == "UTC"
            client.post("/logout", data={"csrf_token": base.csrf(html)})
            assert client.get("/admin/display", follow_redirects=False).status_code == 303
            assert client.post("/admin/display", data=data, follow_redirects=False).status_code in (303, 403)
    finally:
        with session_scope() as db:
            row = db.get(AppSetting, settings_store.DISPLAY_KEY)
            if row:
                db.delete(row)
        settings_store._reset_cache()


@pytest.mark.parametrize("zone", ["", "../UTC", "Invalid/Zone"])
def test_invalid_zones_rejected(zone):
    with pytest.raises(ValueError, match="časová zóna"):
        settings_store.save_display({"display_timezone": zone, "display_language": "sk"})
