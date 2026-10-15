"""Sign-in and display settings editable in the web UI.

Values saved in the UI are stored encrypted in `app_settings` and override the same
variables from `.env`. `effective_settings()` returns the merged configuration.
"""

import logging
import threading
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import Settings, get_settings
from .db import session_scope
from .models import AppSetting
from .security import decrypt_json, encrypt_json

log = logging.getLogger(__name__)

AUTH_KEY = "auth"
DISPLAY_KEY = "display"
DISPLAY_FIELDS = {"display_timezone", "display_language"}
BOOL_FIELDS = {
    "local_login_enabled", "ldap_enabled", "ldap_start_tls", "ldap_tls_verify", "ldap_nested_groups", "oidc_enabled",
}
TEXT_FIELDS = {
    "default_role",
    "ldap_server", "ldap_ca_file", "ldap_bind_dn", "ldap_upn_suffix", "ldap_base_dn", "ldap_user_filter",
    "ldap_allowed_group", "ldap_admin_group", "ldap_attr_display_name", "ldap_attr_mail",
    "oidc_discovery_url", "oidc_client_id", "oidc_scopes", "oidc_button_label", "oidc_username_claim",
    "oidc_groups_claim", "oidc_allowed_groups", "oidc_admin_groups",
}
SECRET_FIELDS = {"ldap_bind_password", "oidc_client_secret"}
AUTH_FIELDS = BOOL_FIELDS | TEXT_FIELDS | SECRET_FIELDS

_lock = threading.Lock()
_overrides: dict | None = None


def overrides() -> dict:
    global _overrides
    if _overrides is None:
        try:
            with session_scope() as db:
                row = db.get(AppSetting, AUTH_KEY)
                data = decrypt_json(row.value) if row else {}
                display = db.get(AppSetting, DISPLAY_KEY)
                display_data = decrypt_json(display.value) if display else {}
        except Exception as exc:  # noqa: BLE001 – table missing before init_db, or SECRET_KEY changed
            log.warning("Stored application settings not loaded: %s", exc)
            data = {}
            display_data = {}
        with _lock:
            _overrides = {k: v for k, v in data.items() if k in AUTH_FIELDS}
            _overrides.update({k: v for k, v in display_data.items() if k in DISPLAY_FIELDS})
    return _overrides


def effective_settings() -> Settings:
    ov = overrides()
    base = get_settings()
    return base.model_copy(update=ov) if ov else base


def _reset_cache():
    global _overrides
    with _lock:
        _overrides = None


def save_auth(values: dict, user: str | None = None):
    data = {k: v for k, v in values.items() if k in AUTH_FIELDS}
    with session_scope() as db:
        row = db.get(AppSetting, AUTH_KEY) or AppSetting(key=AUTH_KEY, value="")
        row.value = encrypt_json(data)
        row.updated_by = user
        db.add(row)
    _reset_cache()


def clear_auth():
    with session_scope() as db:
        row = db.get(AppSetting, AUTH_KEY)
        if row:
            db.delete(row)
    _reset_cache()


def save_display(values: dict, user: str | None = None):
    zone = str(values.get("display_timezone") or "").strip()
    language = str(values.get("display_language") or "")
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("Neplatná časová zóna. Použite názov ako Europe/Bratislava.") from exc
    if language not in ("auto", "sk", "en"):
        raise ValueError("Neplatný jazyk.")
    with session_scope() as db:
        row = db.get(AppSetting, DISPLAY_KEY) or AppSetting(key=DISPLAY_KEY, value="")
        row.value = encrypt_json({"display_timezone": zone, "display_language": language})
        row.updated_by = user
        db.add(row)
    _reset_cache()


def parse_form(form, current: Settings) -> dict:
    """Read the sign-in form. Empty secret fields keep the stored value."""
    out: dict = {}
    for name in BOOL_FIELDS:
        out[name] = form.get(name) == "on"
    for name in TEXT_FIELDS:
        out[name] = str(form.get(name) or "").strip()
    for name in SECRET_FIELDS:
        value = str(form.get(name) or "")
        out[name] = value if value else getattr(current, name)
    if out["default_role"] not in ("admin", "viewer"):
        out["default_role"] = "viewer"
    return out
