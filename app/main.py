import logging
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from .config import get_settings
from .db import init_db, session_scope
from .ingest.service import refresh_dns, run_all_sources
from .models import User
from .routes import admin, auth, pages
from .security import LoginRequired, hash_password
from .web import render

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("dmarcula")
settings = get_settings()
scheduler = BackgroundScheduler(timezone="UTC")


def bootstrap_admin():
    with session_scope() as db:
        if db.scalar(select(func.count(User.id))):
            return
        if not settings.admin_password:
            log.warning("No users exist and ADMIN_PASSWORD is empty – set it to create the first admin account.")
            return
        db.add(User(username=settings.admin_username.lower(), password_hash=hash_password(settings.admin_password),
                    role="admin", auth_source="local", display_name="Administrátor"))
        log.info("Bootstrap admin '%s' created", settings.admin_username)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if len(settings.secret_key) < 16:
        raise RuntimeError("SECRET_KEY must be set to a random string of at least 16 characters")
    if settings.secret_key.startswith("change-me"):
        log.warning("SECRET_KEY is the default value – set a long random SECRET_KEY in production!")
    init_db()
    bootstrap_admin()
    if settings.scheduler_enabled:
        scheduler.add_job(run_all_sources, "interval", minutes=settings.poll_interval_minutes, id="mail",
                          max_instances=1, coalesce=True)
        scheduler.add_job(refresh_dns, "interval", hours=settings.dns_refresh_hours, id="dns",
                          max_instances=1, coalesce=True)
        scheduler.start()
    else:
        log.info("Built-in scheduler disabled – run 'python -m app.manage poll' from cron")
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(title=settings.app_name, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="dmarcula_session",
    max_age=settings.session_max_age_hours * 3600,
    same_site="lax",
    https_only=settings.cookie_secure,
)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(pages.router)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


@app.exception_handler(LoginRequired)
async def login_required(request: Request, _exc: LoginRequired):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Unauthorized"}, 401)
    if request.method == "GET":
        request.session["next"] = str(request.url.path) + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse("/login", 303)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": exc.detail}, exc.status_code)
    return render(request, "error.html", {"code": exc.status_code, "detail": exc.detail}, exc.status_code)


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"status": "ok"}
