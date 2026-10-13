"""LDAP / Active Directory authentication."""

import logging
import ssl
from dataclasses import dataclass

from ldap3 import ALL, SUBTREE, Connection, Server, Tls
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars

from ..config import get_settings

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


def _server():
    s = get_settings()
    tls = None
    if s.ldap_server.lower().startswith("ldaps://") or s.ldap_start_tls:
        tls = Tls(
            validate=ssl.CERT_REQUIRED if s.ldap_tls_verify else ssl.CERT_NONE,
            ca_certs_file=s.ldap_ca_file or None,
        )
    return Server(s.ldap_server, get_info=ALL, tls=tls, connect_timeout=8)


def _bind(server, user: str, password: str) -> Connection:
    s = get_settings()
    conn = Connection(server, user=user, password=password, receive_timeout=15, raise_exceptions=False)
    if s.ldap_start_tls and not s.ldap_server.lower().startswith("ldaps://"):
        conn.open()
        conn.start_tls()
    if not conn.bind():
        raise LdapError("bind_failed")
    return conn


def _in_group(conn: Connection, user_dn: str, member_of: list[str], group_dn: str) -> bool:
    if not group_dn:
        return False
    if group_dn.lower() in (g.lower() for g in member_of):
        return True
    if get_settings().ldap_nested_groups:
        flt = f"(&(distinguishedName={escape_filter_chars(user_dn)})(memberOf:{MATCHING_RULE_IN_CHAIN}:={escape_filter_chars(group_dn)}))"
        conn.search(get_settings().ldap_base_dn, flt, SUBTREE, attributes=["cn"])
        return bool(conn.entries)
    return False


def authenticate(username: str, password: str) -> LdapIdentity | None:
    s = get_settings()
    if not s.ldap_enabled or not username or not password:
        return None
    attrs = [s.ldap_attr_display_name, s.ldap_attr_mail, "memberOf", "sAMAccountName", "userPrincipalName"]
    try:
        server = _server()
        if s.ldap_bind_dn:
            conn = _bind(server, s.ldap_bind_dn, s.ldap_bind_password)
        else:
            principal = username if ("@" in username or "\\" in username or not s.ldap_upn_suffix) else f"{username}@{s.ldap_upn_suffix}"
            conn = _bind(server, principal, password)

        lookup = username.split("\\")[-1]
        flt = s.ldap_user_filter.replace("{username}", escape_filter_chars(lookup))
        conn.search(s.ldap_base_dn, flt, SUBTREE, attributes=attrs, size_limit=2)
        if len(conn.entries) != 1:
            log.info("LDAP user %s not found or ambiguous", lookup)
            return None
        entry = conn.entries[0]
        user_dn = entry.entry_dn
        data = entry.entry_attributes_as_dict

        if s.ldap_bind_dn:
            # verify the user's own password
            _bind(server, user_dn, password).unbind()

        member_of = [str(g) for g in data.get("memberOf", [])]
        if s.ldap_allowed_group and not _in_group(conn, user_dn, member_of, s.ldap_allowed_group):
            log.info("LDAP user %s not in allowed group", lookup)
            return None
        is_admin = _in_group(conn, user_dn, member_of, s.ldap_admin_group)
        conn.unbind()

        def first(key):
            v = data.get(key) or []
            return str(v[0]) if v else None

        return LdapIdentity(
            username=(first("sAMAccountName") or lookup).lower(),
            display_name=first(s.ldap_attr_display_name),
            email=first(s.ldap_attr_mail),
            is_admin=is_admin,
        )
    except LdapError:
        return None
    except LDAPException as exc:
        log.warning("LDAP error: %s", exc)
        return None
