"""Source IP enrichment: reverse DNS, optional GeoIP (MaxMind GeoLite2 .mmdb)."""

import ipaddress
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import dns.reversename
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import IpInfo, utcnow
from .dns_checks import _resolver
from .senders import base_domain

log = logging.getLogger(__name__)

_readers: dict[str, object] = {}


def _reader(path: str):
    if not path:
        return None
    if path not in _readers:
        try:
            import maxminddb

            _readers[path] = maxminddb.open_database(path)
        except Exception as exc:  # noqa: BLE001
            log.warning("GeoIP DB %s unavailable: %s", path, exc)
            _readers[path] = None
    return _readers[path]


def reverse_dns(ip: str) -> str | None:
    try:
        ans = _resolver().resolve(dns.reversename.from_address(ip), "PTR")
        return str(ans[0]).rstrip(".").lower()
    except Exception:  # noqa: BLE001
        return None


def _lookup(ip: str) -> dict:
    s = get_settings()
    info: dict = {"ip": ip, "ptr": None, "country": None, "country_code": None, "asn": None, "asn_org": None}
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        return info
    if s.resolve_ptr:
        info["ptr"] = reverse_dns(ip)
    country = _reader(s.geoip_country_db)
    if country:
        rec = country.get(ip) or {}
        c = rec.get("country") or rec.get("registered_country") or {}
        info["country"] = (c.get("names") or {}).get("en")
        info["country_code"] = c.get("iso_code")
    asn = _reader(s.geoip_asn_db)
    if asn:
        rec = asn.get(ip) or {}
        info["asn"] = rec.get("autonomous_system_number")
        info["asn_org"] = rec.get("autonomous_system_organization")
    return info


def enrich_ips(db: Session, ips: set[str], max_age_days: int = 14) -> dict[str, IpInfo]:
    """Return IpInfo for each IP, resolving uncached / stale entries in parallel."""
    ips = {i for i in ips if i}
    cached = {i.ip: i for i in db.query(IpInfo).filter(IpInfo.ip.in_(ips)).all()} if ips else {}
    stale_before = utcnow() - timedelta(days=max_age_days)
    todo = [ip for ip in ips if ip not in cached or cached[ip].checked_at < stale_before]
    if todo:
        with ThreadPoolExecutor(max_workers=16) as pool:
            for info in pool.map(_lookup, todo):
                obj = cached.get(info["ip"]) or IpInfo(ip=info["ip"])
                for k in ("ptr", "country", "country_code", "asn", "asn_org"):
                    setattr(obj, k, info[k])
                obj.base_domain = base_domain(info["ptr"])
                obj.checked_at = utcnow()
                db.add(obj)
                cached[info["ip"]] = obj
        db.flush()
    return cached
