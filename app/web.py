"""Shared template environment and helpers."""

import json
from datetime import date, datetime
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from .analysis.senders import CATEGORIES
from .config import get_settings
from .security import csrf_token

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _num(value) -> str:
    try:
        return f"{int(value):,}".replace(",", " ")
    except (TypeError, ValueError):
        return "–"


def _pct(value) -> str:
    return "–" if value is None else f"{value:.1f} %".replace(".", ",")


def _dt(value) -> str:
    if not value:
        return "–"
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    return str(value)


def _json_default(o):
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    raise TypeError(type(o))


templates.env.filters["num"] = _num
templates.env.filters["pct"] = _pct
templates.env.filters["dt"] = _dt
def _jsonify(value) -> Markup:
    """JSON safe to embed in <script> blocks (escapes <, > and & so it cannot close the tag)."""
    text = json.dumps(value, default=_json_default, ensure_ascii=False)
    return Markup(text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


templates.env.filters["jsonify"] = _jsonify
templates.env.globals["CATEGORIES"] = CATEGORIES


def render(request: Request, name: str, ctx: dict | None = None, status_code: int = 200):
    s = get_settings()
    base = {
        "request": request,
        "settings": s,
        "user": getattr(request.state, "user", None),
        "csrf": csrf_token(request),
        "flash": request.session.pop("flash", None),
        "path": request.url.path,
    }
    base.update(ctx or {})
    return templates.TemplateResponse(request, name, base, status_code=status_code)


def flash(request: Request, message: str, level: str = "ok"):
    request.session["flash"] = {"msg": message, "level": level}
