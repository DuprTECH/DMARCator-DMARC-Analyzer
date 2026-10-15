# ruff: noqa: I001  – the test DB must be configured before app modules are imported
import tests.test_app as base

from fastapi.testclient import TestClient  # noqa: E402

from app import manage, settings_store  # noqa: E402
from app.main import app  # noqa: E402

FORM = {
    "local_login_enabled": "on", "default_role": "viewer",
    "ldap_enabled": "on", "ldap_server": "ldap://127.0.0.1:1", "ldap_tls_verify": "on",
    "ldap_base_dn": "DC=example,DC=local", "ldap_bind_dn": "CN=svc,DC=example,DC=local", "ldap_bind_password": "S3rvice-pw",
    "ldap_user_filter": "(sAMAccountName={username})", "ldap_attr_display_name": "displayName", "ldap_attr_mail": "mail",
    "oidc_discovery_url": "http://127.0.0.1:1/.well-known/openid-configuration", "oidc_client_id": "abc",
    "oidc_scopes": "openid profile email", "oidc_button_label": "Prihlásiť cez Microsoft",
    "oidc_username_claim": "preferred_username", "oidc_groups_claim": "groups",
}


def test_sign_in_settings_in_web_ui():
    try:
        with TestClient(app) as client:
            base.login(client)
            page = client.get("/admin/auth")
            assert page.status_code == 200
            token = base.csrf(page.text)

            r = client.post("/admin/auth", data={**FORM, "csrf_token": token, "action": "save"}, follow_redirects=False)
            assert r.status_code == 303
            eff = settings_store.effective_settings()
            assert eff.ldap_enabled and eff.ldap_bind_password == "S3rvice-pw" and eff.ldap_server == "ldap://127.0.0.1:1"
            assert "S3rvice-pw" not in client.get("/admin/auth").text

            # empty secret on the next save keeps the stored one
            client.post("/admin/auth", data={**FORM, "ldap_bind_password": "", "csrf_token": token, "action": "save"})
            assert settings_store.effective_settings().ldap_bind_password == "S3rvice-pw"

            # the login page reflects the settings without a restart
            client.post("/logout", data={"csrf_token": token})
            assert "LDAP" in client.get("/login").text
            base.login(client)
            token = base.csrf(client.get("/admin/auth").text)

            # LDAP test reports the failing step and saves nothing
            r = client.post("/admin/auth", data={**FORM, "ldap_server": "ldap://127.0.0.1:2", "csrf_token": token,
                                                 "action": "test_ldap", "test_username": "john", "test_password": "x"})
            assert "zlyhalo" in r.text or "Chyba LDAP" in r.text
            assert settings_store.effective_settings().ldap_server == "ldap://127.0.0.1:1"

            r = client.post("/admin/auth", data={**FORM, "csrf_token": token, "action": "test_oidc"})
            assert "Discovery URL sa nepodarilo načítať" in r.text and "/auth/oidc/callback" in r.text

            # a local admin cannot lock themselves out
            form = {k: v for k, v in FORM.items() if k != "local_login_enabled"}
            r = client.post("/admin/auth", data={**form, "csrf_token": token, "action": "save"})
            assert "Lokálne prihlásenie môžete vypnúť" in r.text
            assert settings_store.effective_settings().local_login_enabled

            client.cookies.set("lang", "en")
            assert "Sign-in (LDAP / SSO)" in client.get("/admin/auth").text
            client.cookies.set("lang", "sk")
        assert manage.main(["auth-reset"]) == 0
        assert not settings_store.effective_settings().ldap_enabled
    finally:
        settings_store.clear_auth()


def test_new_external_users_wait_for_approval():
    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from app.db import init_db, session_scope
    from app.main import app
    from app.models import User
    from app.routes.auth import upsert_external
    from app.security import hash_password
    from tests.test_app import csrf

    init_db()
    with session_scope() as db:
        u = upsert_external(db, "oidc", "new.user@example.com", "New User", None, None)
        assert u.role == "none"  # DEFAULT_ROLE=none
        u.role = "viewer"  # granted by an administrator
        db.commit()
        assert upsert_external(db, "oidc", "new.user@example.com", None, None, False).role == "viewer"  # kept
        assert upsert_external(db, "oidc", "new.user@example.com", None, None, True).role == "admin"
        assert upsert_external(db, "oidc", "new.user@example.com", None, None, False).role == "none"  # left the group
        db.add(User(username="pending.local", auth_source="local", role="none", password_hash=hash_password("Pending-pass-1")))

    with TestClient(app) as client:
        page = client.get("/login")
        r = client.post("/login", data={"username": "pending.local", "password": "Pending-pass-1", "csrf_token": csrf(page.text)},
                        follow_redirects=False)
        assert r.status_code == 403 and "čaká na schválenie" in r.text
        assert client.get("/", follow_redirects=False).headers["location"] == "/login"
    with session_scope() as db:
        assert db.scalar(select(User.role).where(User.username == "pending.local")) == "none"
