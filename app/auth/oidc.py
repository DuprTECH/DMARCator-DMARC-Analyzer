"""OpenID Connect SSO (Entra ID, Keycloak, Okta, ADFS, Google Workspace ...)."""

import threading

import httpx
from authlib.integrations.starlette_client import OAuth

from ..config import Settings

_lock = threading.Lock()
_clients: dict[tuple, object] = {}


def _settings(s: Settings | None) -> Settings:
    if s is not None:
        return s
    from ..settings_store import effective_settings

    return effective_settings()


def client(s: Settings | None = None):
    """OAuth client for the current settings – re-created when the settings change in the UI."""
    s = _settings(s)
    if not s.oidc_enabled or not s.oidc_discovery_url or not s.oidc_client_id:
        return None
    key = (s.oidc_discovery_url, s.oidc_client_id, s.oidc_client_secret, s.oidc_scopes)
    with _lock:
        if key not in _clients:
            oauth = OAuth()
            oauth.register(
                name="oidc",
                server_metadata_url=s.oidc_discovery_url,
                client_id=s.oidc_client_id,
                client_secret=s.oidc_client_secret,
                client_kwargs={"scope": s.oidc_scopes},
            )
            _clients.clear()
            _clients[key] = oauth.oidc
        return _clients[key]


def map_claims(claims: dict, s: Settings | None = None) -> tuple[str, str | None, str | None, list[str]]:
    s = _settings(s)
    username = (
        claims.get(s.oidc_username_claim)
        or claims.get("upn")
        or claims.get("email")
        or claims.get("sub")
    )
    groups = claims.get(s.oidc_groups_claim) or []
    if isinstance(groups, str):
        groups = [groups]
    # Entra ID app roles always arrive in "roles" – honour them too
    groups = list(groups) + list(claims.get("roles") or [])
    return str(username).lower(), claims.get("name"), claims.get("email") or claims.get("upn"), groups


def test_discovery(s: Settings) -> list[dict]:
    """Check the OIDC configuration without a browser round-trip. Returns UI trace steps."""
    steps: list[dict] = []

    def note(ok: bool, msg: str, **args):
        steps.append({"ok": ok, "msg": msg, "args": {k: str(v) for k, v in args.items()}})

    if not s.oidc_discovery_url or not s.oidc_client_id:
        note(False, "Vyplňte Discovery URL a Client ID.")
        return steps
    try:
        r = httpx.get(s.oidc_discovery_url, timeout=10, follow_redirects=True)
        r.raise_for_status()
        meta = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        note(False, "Discovery URL sa nepodarilo načítať: {error}", error=f"{exc.__class__.__name__}: {exc}")
        return steps
    note(True, "Discovery dokument načítaný, issuer {issuer}", issuer=meta.get("issuer", "?"))
    for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not meta.get(field):
            note(False, "V discovery dokumente chýba {field}", field=field)
    if not s.oidc_client_secret:
        note(False, "Chýba client secret.")
    note(True, "Redirect URI, ktorú treba zaregistrovať u poskytovateľa: {uri}",
         uri=f"{s.base_url.rstrip('/')}/auth/oidc/callback")
    if not s.base_url.startswith("https://"):
        note(False, "BASE_URL nie je https:// – Entra ID a väčšina poskytovateľov vyžaduje HTTPS.")
    return steps
