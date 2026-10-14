import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import ldap_auth, oidc
from ..db import get_db
from ..models import User, utcnow
from ..security import (
    login_session,
    throttle_check,
    throttle_fail,
    throttle_reset,
    verify_csrf,
    verify_password,
)
from ..settings_store import effective_settings
from ..web import render

log = logging.getLogger(__name__)
router = APIRouter()


def _safe_next(target: str | None) -> str:
    return target if target and target.startswith("/") and not target.startswith("//") else "/"


def upsert_external(db: Session, source: str, username: str, display: str | None, email: str | None,
                    mapped_role: str | None) -> User | None:
    user = db.scalar(select(User).where(func.lower(User.username) == username.lower()))
    if user and user.auth_source != source:
        log.warning("Login via %s refused: username %s belongs to %s account", source, username, user.auth_source)
        return None
    if not user:
        user = User(username=username, auth_source=source, role=mapped_role or effective_settings().default_role)
        db.add(user)
    elif mapped_role:
        user.role = mapped_role
    user.display_name = display or user.display_name
    user.email = email or user.email
    user.last_login = utcnow()
    db.commit()
    return user if user.is_active else None


@router.get("/login")
def login_page(request: Request):
    if request.session.get("uid"):
        return RedirectResponse("/", 303)
    return render(request, "login.html")


@router.post("/login", dependencies=[Depends(verify_csrf)])
def login_submit(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    s = effective_settings()
    username = username.strip()
    key = f"{request.client.host if request.client else '-'}:{username.lower()}"
    if not throttle_check(key):
        return render(request, "login.html", {"error": "Príliš veľa neúspešných pokusov. Skúste to o 5 minút."}, 429)

    user = None
    local = db.scalar(select(User).where(func.lower(User.username) == username.lower(), User.auth_source == "local"))
    if local and s.local_login_enabled and local.is_active and verify_password(password, local.password_hash):
        user = local
        user.last_login = utcnow()
        db.commit()
    elif not local and s.ldap_enabled:
        ident = ldap_auth.authenticate(username, password, s)
        if ident:
            role = ("admin" if ident.is_admin else s.default_role) if s.ldap_admin_group else None
            user = upsert_external(db, "ldap", ident.username, ident.display_name, ident.email, role)

    if not user:
        throttle_fail(key)
        return render(request, "login.html", {"error": "Nesprávne meno alebo heslo.", "username": username}, 401)
    throttle_reset(key)
    target = _safe_next(request.session.get("next"))
    login_session(request, user)
    return RedirectResponse(target, 303)


@router.get("/auth/oidc/login")
async def oidc_login(request: Request):
    client = oidc.client()
    if not client:
        return RedirectResponse("/login", 303)
    return await client.authorize_redirect(request, f"{effective_settings().base_url.rstrip('/')}/auth/oidc/callback")


@router.get("/auth/oidc/callback")
async def oidc_callback(request: Request, db: Session = Depends(get_db)):
    s = effective_settings()
    client = oidc.client(s)
    if not client:
        return RedirectResponse("/login", 303)
    try:
        token = await client.authorize_access_token(request)
    except Exception as exc:  # noqa: BLE001
        log.warning("OIDC callback failed: %s", exc)
        return render(request, "login.html", {"error": "SSO prihlásenie zlyhalo."}, 401)
    claims = token.get("userinfo") or {}
    username, name, email, groups = oidc.map_claims(dict(claims), s)
    allowed = s.oidc_allowed_group_list
    if allowed and not set(allowed) & set(groups):
        return render(request, "login.html", {"error": "Nemáte oprávnenie na prístup do aplikácie."}, 403)
    role = None
    if s.oidc_admin_group_list:
        role = "admin" if set(s.oidc_admin_group_list) & set(groups) else s.default_role
    user = upsert_external(db, "oidc", username, name, email, role)
    if not user:
        return render(request, "login.html", {"error": "Účet je zablokovaný alebo koliduje s iným účtom."}, 403)
    target = _safe_next(request.session.get("next"))
    login_session(request, user)
    return RedirectResponse(target, 303)


@router.post("/logout", dependencies=[Depends(verify_csrf)])
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", 303)
