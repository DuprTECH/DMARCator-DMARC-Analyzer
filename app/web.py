"""Shared template environment and helpers."""

import hashlib
import json
from contextvars import ContextVar
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from . import __version__
from .analysis.senders import CATEGORIES
from .i18n import LANGUAGES, get_language, js_strings, translate
from .security import csrf_token
from .settings_store import effective_settings

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

AUTHOR = "DuprTECH"
AUTHOR_URL = "https://github.com/DuprTECH/DMARCator-DMARC-Analyzer"
LICENSE_URL = AUTHOR_URL + "/blob/main/LICENSE"
display_zone: ContextVar[ZoneInfo] = ContextVar("display_zone", default=ZoneInfo("UTC"))


def _num(value) -> str:
    try:
        text = f"{int(value):,}"
    except (TypeError, ValueError):
        return "–"
    return text if get_language() == "en" else text.replace(",", " ")


def _pct(value) -> str:
    if value is None:
        return "–"
    text = f"{value:.1f} %"
    return text if get_language() == "en" else text.replace(".", ",")


def _dt(value) -> str:
    if not value:
        return "–"
    if isinstance(value, str):
        value = date.fromisoformat(value) if len(value) == 10 else datetime.fromisoformat(value)
    en = get_language() == "en"
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        value = value.astimezone(display_zone.get())
        return value.strftime("%Y-%m-%d %H:%M" if en else "%d.%m.%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d" if en else "%d.%m.%Y")
    return str(value)


def _json_default(o):
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    raise TypeError(type(o))


def _jsonify(value) -> Markup:
    """JSON safe to embed in <script> blocks (escapes <, > and & so it cannot close the tag)."""
    text = json.dumps(value, default=_json_default, ensure_ascii=False)
    return Markup(text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


templates.env.filters["num"] = _num
templates.env.filters["pct"] = _pct
templates.env.filters["dt"] = _dt
templates.env.filters["jsonify"] = _jsonify
templates.env.filters["t"] = translate
templates.env.globals["_"] = translate
templates.env.globals["CATEGORIES"] = CATEGORIES
templates.env.globals["LANGUAGES"] = LANGUAGES
templates.env.globals["AUTHOR"] = AUTHOR
templates.env.globals["AUTHOR_URL"] = AUTHOR_URL
templates.env.globals["VERSION"] = __version__
# changes with every edit of the static files, so browsers never keep an outdated app.js / app.css
templates.env.globals["ASSET_V"] = hashlib.sha256(b"".join(
    p.read_bytes() for p in sorted((Path(__file__).parent / "static").rglob("*")) if p.is_file())).hexdigest()[:10]
templates.env.globals["LICENSE_URL"] = LICENSE_URL


def render(request: Request, name: str, ctx: dict | None = None, status_code: int = 200):
    s = effective_settings()
    base = {
        "request": request,
        "settings": s,
        "user": getattr(request.state, "user", None),
        "csrf": csrf_token(request),
        "flash": request.session.pop("flash", None),
        "path": request.url.path,
        "lang": get_language(),
        "js_i18n": js_strings(),
        "year": datetime.now().year,
        "display_timezone": display_zone.get().key,
    }
    base.update(ctx or {})
    return templates.TemplateResponse(request, name, base, status_code=status_code)


def flash(request: Request, message: str, level: str = "ok"):
    request.session["flash"] = {"msg": message, "level": level}
