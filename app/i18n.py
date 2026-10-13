"""User-interface translations.

Slovak is the source language: every UI string in templates and code is written in Slovak and
looked up in `EN` (app/i18n_en.py) when English is selected. Keys may contain `{placeholders}`;
such keys also translate *already rendered* Slovak text (e.g. DNS findings stored in the
database) by matching it against the key as a pattern.
"""

import contextvars
import re
from functools import lru_cache

from .i18n_en import EN

LANGUAGES = {"sk": "Slovenčina", "en": "English"}
DEFAULT_LANGUAGE = "sk"

_lang: contextvars.ContextVar[str] = contextvars.ContextVar("lang", default=DEFAULT_LANGUAGE)


def set_language(code: str | None) -> str:
    code = code if code in LANGUAGES else DEFAULT_LANGUAGE
    _lang.set(code)
    return code


def get_language() -> str:
    return _lang.get()


@lru_cache(maxsize=1)
def _patterns() -> list[tuple[re.Pattern, str]]:
    out = []
    for sk, en in EN.items():
        if "{" not in sk:
            continue
        parts = re.split(r"\{(\w+)\}", sk)
        rx = "".join(re.escape(p) if i % 2 == 0 else f"(?P<{p}>.+?)" for i, p in enumerate(parts))
        literal = sum(len(p) for i, p in enumerate(parts) if i % 2 == 0)
        out.append((re.compile(f"^{rx}$", re.S), en, literal))
    out.sort(key=lambda x: -x[2])  # most specific pattern first
    return [(rx, en) for rx, en, _ in out]


def translate(text, **kwargs):
    """Translate a Slovak UI string (optionally a template with {placeholders}) to the current language."""
    if not isinstance(text, str) or not text:
        return text
    if _lang.get() == "sk":
        return text.format(**kwargs) if kwargs else text
    if text in EN:
        return EN[text].format(**kwargs) if kwargs else EN[text]
    if not kwargs:
        for rx, en in _patterns():
            m = rx.match(text)
            if m:
                return en.format(**{k: translate(v) for k, v in m.groupdict().items()})
    return text.format(**kwargs) if kwargs else text


_ = translate

# Strings used by static/js/app.js (chart labels, tooltips) – handed to the browser per language.
JS_STRINGS = [
    "Prešlo DMARC", "Preposlané (fail)", "Zlyhalo", "Spolu", "súlad", "Súlad", "Správ", "správ",
    "Zlyhalo:", "Mapa nie je dostupná (offline).", "V súlade", "Preposlané", "Nesúlad (legitímny zdroj)",
    "Hrozba / neznámy", "SPF + DKIM", "Iba SPF", "Iba DKIM", "Žiadne", "Neznáma", "Neznámy",
]


def js_strings() -> dict:
    return {s: translate(s) for s in JS_STRINGS} if _lang.get() != "sk" else {}
