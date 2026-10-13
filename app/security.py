import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import defaultdict

from cryptography.fernet import Fernet
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import User

settings = get_settings()

# ---------------------------------------------------------------- passwords


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=64)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        return False
    _, salt_b64, digest_b64 = stored.split("$", 2)
    digest = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), n=2**14, r=8, p=1, dklen=64)
    return hmac.compare_digest(digest, base64.b64decode(digest_b64))


# ------------------------------------------------------- secrets at rest


def _fernet() -> Fernet:
    key = hashlib.sha256(("dmarcula-config:" + settings.secret_key).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_json(data: dict) -> str:
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt_json(token: str) -> dict:
    return json.loads(_fernet().decrypt(token.encode()))


# ------------------------------------------------------------------- CSRF


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


async def verify_csrf(request: Request):
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    sent = request.headers.get("x-csrf-token")
    if not sent:
        form = await request.form()
        sent = form.get("csrf_token")
    if not sent or not hmac.compare_digest(str(sent), request.session.get("csrf", "")):
        raise HTTPException(status_code=403, detail="Neplatný CSRF token")


# ------------------------------------------------------- login throttling

_failures: dict[str, list[float]] = defaultdict(list)


def throttle_check(key: str, limit: int = 8, window: int = 300) -> bool:
    now = time.time()
    _failures[key] = [t for t in _failures[key] if now - t < window]
    return len(_failures[key]) < limit


def throttle_fail(key: str):
    _failures[key].append(time.time())


def throttle_reset(key: str):
    _failures.pop(key, None)


# ------------------------------------------------------------ auth deps


class LoginRequired(Exception):
    pass


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("uid")
    issued = request.session.get("iat", 0)
    if not uid or time.time() - issued > settings.session_max_age_hours * 3600:
        request.session.clear()
        raise LoginRequired()
    user = db.get(User, uid)
    if not user or not user.is_active:
        request.session.clear()
        raise LoginRequired()
    request.state.user = user
    return user


def admin_required(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Vyžaduje rolu administrátora")
    return user


def login_session(request: Request, user: User):
    request.session.clear()
    request.session["uid"] = user.id
    request.session["iat"] = int(time.time())
    csrf_token(request)
