"""LDAP / Active Directory authentication."""

import logging
import ssl
from dataclasses import dataclass

from ldap3 import ALL, SUBTREE, Connection, Server, Tls
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars

from ..config import Settings

log = logging.getLogger(__name__)

MATCHING_RULE_IN_CHAIN = "1.2.840.113556.1.4.1941"


@dataclass
class LdapIdentity:
    username: str
    display_name: str | None
    email: str | None
    is_admin: bool


class LdapError(Exception):
    pass


def _server(s: Settings):
    tls = None
    if s.ldap_server.lower().startswith("ldaps://") or s.ldap_start_tls:
        tls = Tls(
            validate=ssl.CERT_REQUIRED if s.ldap_tls_verify else ssl.CERT_NONE,
            ca_certs_file=s.ldap_ca_file or None,
        )
    return Server(s.ldap_server, get_info=ALL, tls=tls, connect_timeout=8)


def _bind(s: Settings, server, user: str, password: str) -> Connection:
    conn = Connection(server, user=user, password=password, receive_timeout=15, raise_exceptions=False)
    if s.ldap_start_tls and not s.ldap_server.lower().startswith("ldaps://"):
        conn.open()
        conn.start_tls()
    if not conn.bind():
        result = conn.result or {}
        raise LdapError(f"{result.get('description') or 'bind failed'} {result.get('message') or ''}".strip())
    return conn


def _in_group(s: Settings, conn: Connection, user_dn: str, member_of: list[str], group_dn: str) -> bool:
    if not group_dn:
        return False
    if group_dn.lower() in (g.lower() for g in member_of):
        return True
    if s.ldap_nested_groups:
        flt = f"(&(distinguishedName={escape_filter_chars(user_dn)})(memberOf:{MATCHING_RULE_IN_CHAIN}:={escape_filter_chars(group_dn)}))"
        conn.search(s.ldap_base_dn, flt, SUBTREE, attributes=["cn"])
        return bool(conn.entries)
    return False


def authenticate(username: str, password: str, s: Settings | None = None, trace: list | None = None) -> LdapIdentity | None:
    """Verify credentials against LDAP. `trace` (optional) collects step-by-step results for the UI test."""
    if s is None:
        from ..settings_store import effective_settings

        s = effective_settings()

    def note(ok: bool, msg: str, **args):
        if trace is not None:
            trace.append({"ok": ok, "msg": msg, "args": {k: str(v) for k, v in args.items()}})

    if not s.ldap_enabled or not username or not password:
        note(False, "LDAP je vypnuté alebo chýba meno či heslo.")
        return None
    attrs = [s.ldap_attr_display_name, s.ldap_attr_mail, "memberOf", "sAMAccountName", "userPrincipalName"]
    step = "connect"
    try:
        server = _server(s)
        if s.ldap_bind_dn:
            step = "service"
            conn = _bind(s, server, s.ldap_bind_dn, s.ldap_bind_password)
            note(True, "Pripojenie k {server} a prihlásenie servisným účtom {dn}", server=s.ldap_server, dn=s.ldap_bind_dn)
        else:
            step = "direct"
            principal = username if ("@" in username or "\\" in username or not s.ldap_upn_suffix) else f"{username}@{s.ldap_upn_suffix}"
            conn = _bind(s, server, principal, password)
            note(True, "Pripojenie k {server} a prihlásenie ako {user}", server=s.ldap_server, user=principal)

        step = "search"
        lookup = username.split("\\")[-1]
        flt = s.ldap_user_filter.replace("{username}", escape_filter_chars(lookup))
        conn.search(s.ldap_base_dn, flt, SUBTREE, attributes=attrs, size_limit=2)
        if len(conn.entries) != 1:
            note(False, "Vyhľadanie {filter} v {base}: nájdených {n} záznamov (očakáva sa práve 1)",
                 filter=flt, base=s.ldap_base_dn, n=len(conn.entries))
            log.info("LDAP user %s not found or ambiguous", lookup)
            return None
        entry = conn.entries[0]
        user_dn = entry.entry_dn
        data = entry.entry_attributes_as_dict
        note(True, "Používateľ nájdený: {dn}", dn=user_dn)

        if s.ldap_bind_dn:
            step = "password"
            _bind(s, server, user_dn, password).unbind()
            note(True, "Heslo používateľa je správne")

        member_of = [str(g) for g in data.get("memberOf", [])]
        if s.ldap_allowed_group:
            allowed = _in_group(s, conn, user_dn, member_of, s.ldap_allowed_group)
            note(allowed, "Je členom povolenej skupiny {group}" if allowed else "Nie je členom povolenej skupiny {group}",
                 group=s.ldap_allowed_group)
            if not allowed:
                log.info("LDAP user %s not in allowed group", lookup)
                return None
        is_admin = _in_group(s, conn, user_dn, member_of, s.ldap_admin_group)
        if s.ldap_admin_group:
            note(True, "Je členom skupiny administrátorov {group}" if is_admin else "Nie je členom skupiny administrátorov {group}",
                 group=s.ldap_admin_group)
        conn.unbind()

        def first(key):
            v = data.get(key) or []
            return str(v[0]) if v else None

        identity = LdapIdentity(
            username=(first("sAMAccountName") or lookup).lower(),
            display_name=first(s.ldap_attr_display_name),
            email=first(s.ldap_attr_mail),
            is_admin=is_admin,
        )
        note(True, "Prihlásenie by prebehlo ako {user} (rola {role})", user=identity.username,
             role="admin" if is_admin else s.default_role)
        return identity
    except LdapError as exc:
        messages = {
            "service": "Prihlásenie servisným účtom zlyhalo: {error}",
            "direct": "Pripojenie alebo prihlásenie používateľa zlyhalo: {error}",
            "password": "Nesprávne heslo používateľa: {error}",
        }
        note(False, messages.get(step, "Chyba LDAP: {error}"), error=exc)
        return None
    except LDAPException as exc:
        log.warning("LDAP error: %s", exc)
        note(False, "Chyba LDAP: {error}", error=f"{exc.__class__.__name__}: {exc}")
        return None
