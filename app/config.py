from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "DMARCula"
    default_language: str = "sk"  # sk | en – users can switch in the UI
    base_url: str = "http://localhost:8000"
    secret_key: str = "change-me-please-change-me-please"
    database_url: str = "sqlite:///./data/dmarcula.db"
    session_max_age_hours: int = 12
    cookie_secure: bool = False

    # Bootstrap local admin (created only when the user table is empty)
    admin_username: str = "admin"
    admin_password: str = ""
    local_login_enabled: bool = True

    # LDAP / Active Directory
    ldap_enabled: bool = False
    ldap_server: str = ""  # ldaps://dc01.contoso.local:636
    ldap_start_tls: bool = False
    ldap_tls_verify: bool = True
    ldap_ca_file: str = ""
    ldap_bind_dn: str = ""  # service account; empty = bind directly as user
    ldap_bind_password: str = ""
    ldap_upn_suffix: str = ""  # e.g. contoso.local – used when no service account
    ldap_base_dn: str = ""
    ldap_user_filter: str = "(&(objectClass=user)(|(sAMAccountName={username})(userPrincipalName={username})))"
    ldap_allowed_group: str = ""  # DN; empty = every authenticated user
    ldap_admin_group: str = ""  # DN
    ldap_nested_groups: bool = True  # AD LDAP_MATCHING_RULE_IN_CHAIN
    ldap_attr_display_name: str = "displayName"
    ldap_attr_mail: str = "mail"

    # OIDC SSO (Entra ID / Keycloak / Okta / ADFS ...)
    oidc_enabled: bool = False
    oidc_discovery_url: str = ""  # https://login.microsoftonline.com/<tenant>/v2.0/.well-known/openid-configuration
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_scopes: str = "openid profile email"
    oidc_button_label: str = "Prihlásiť cez Microsoft"
    oidc_username_claim: str = "preferred_username"
    oidc_groups_claim: str = "groups"  # Entra: "groups" (object IDs) or "roles" (app roles)
    oidc_allowed_groups: str = ""  # comma separated; empty = everyone in the tenant
    oidc_admin_groups: str = ""  # comma separated

    default_role: str = "viewer"

    # Ingestion
    scheduler_enabled: bool = True  # false = poll from cron via "python -m app.manage poll"
    poll_interval_minutes: int = 15
    dns_refresh_hours: int = 24
    resolve_ptr: bool = True
    dns_servers: str = ""  # comma separated, e.g. "1.1.1.1,8.8.8.8"; empty = system resolvers
    geoip_country_db: str = ""  # path to GeoLite2-Country.mmdb (optional)
    geoip_asn_db: str = ""  # path to GeoLite2-ASN.mmdb (optional)
    max_attachment_mb: int = 50

    @property
    def oidc_allowed_group_list(self) -> list[str]:
        return [g.strip() for g in self.oidc_allowed_groups.split(",") if g.strip()]

    @property
    def oidc_admin_group_list(self) -> list[str]:
        return [g.strip() for g in self.oidc_admin_groups.split(",") if g.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
