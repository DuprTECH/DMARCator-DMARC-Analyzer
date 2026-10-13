"""OpenID Connect SSO (Entra ID, Keycloak, Okta, ADFS, Google Workspace ...)."""

from authlib.integrations.starlette_client import OAuth

from ..config import get_settings

oauth = OAuth()
_registered = False


def client():
    global _registered
    s = get_settings()
    if not s.oidc_enabled:
        return None
    if not _registered:
        oauth.register(
            name="oidc",
            server_metadata_url=s.oidc_discovery_url,
            client_id=s.oidc_client_id,
            client_secret=s.oidc_client_secret,
            client_kwargs={"scope": s.oidc_scopes},
        )
        _registered = True
    return oauth.oidc


def map_claims(claims: dict) -> tuple[str, str | None, str | None, list[str]]:
    s = get_settings()
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
