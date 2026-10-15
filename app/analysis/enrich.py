"""Source IP enrichment: reverse DNS, optional GeoIP (MaxMind GeoLite2 .mmdb)."""

import ipaddress
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import dns.reversename
from sqlalchemy import bindparam, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import AggregateRecord, ForensicReport, IpInfo, utcnow
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


def _valid_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def reverse_dns(ip: str) -> str | None:
    try:
        ans = _resolver().resolve(dns.reversename.from_address(ip), "PTR")
        return str(ans[0]).rstrip(".").lower()
    except Exception:  # noqa: BLE001
        return None


def geo_lookup(ip: str) -> dict:
    """Country and ASN of an IP from the GeoLite2 databases (empty values without them)."""
    s = get_settings()
    info: dict = {"country": None, "country_code": None, "asn": None, "asn_org": None}
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


def geoip_available() -> bool:
    s = get_settings()
    return bool(_reader(s.geoip_country_db) or _reader(s.geoip_asn_db))


def _lookup(ip: str) -> dict:
    info: dict = {"ip": ip, "ptr": None, "country": None, "country_code": None, "asn": None, "asn_org": None}
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        return info
    if get_settings().resolve_ptr:
        info["ptr"] = reverse_dns(ip)
    info.update(geo_lookup(ip))
    return info


def enrich_ips(db: Session, ips: set[str], max_age_days: int = 14) -> dict[str, IpInfo]:
    """Return IpInfo for each IP, resolving uncached / stale entries in parallel."""
    ips = {i for i in ips if i and _valid_ip(i)}
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


def refresh_geoip(db: Session) -> dict:
    """Look up country / ASN again for every known source IP and write them into the stored
    aggregate records and forensic reports (they keep the values from the time of import)."""
    rec, fr = AggregateRecord.__table__, ForensicReport.__table__
    ips = set(db.scalars(select(IpInfo.ip)))
    ips |= set(db.scalars(select(rec.c.source_ip).distinct()))
    ips |= {i for i in db.scalars(select(fr.c.source_ip).distinct()) if i}
    ips = {i for i in ips if _valid_ip(i)}
    cached = {i.ip: i for i in db.scalars(select(IpInfo))}
    rows = []
    for ip in ips:
        geo = geo_lookup(ip)
        obj = cached.get(ip)
        if obj is None:
            obj = IpInfo(ip=ip, checked_at=utcnow())
            db.add(obj)
        for k, v in geo.items():
            setattr(obj, k, v)
        rows.append({"b_ip": ip, "b_country": geo["country"], "b_asn_org": geo["asn_org"]})
    db.flush()
    if rows:
        db.execute(rec.update().where(rec.c.source_ip == bindparam("b_ip"))
                   .values(country=bindparam("b_country"), asn_org=bindparam("b_asn_org")), rows)
        db.execute(fr.update().where(fr.c.source_ip == bindparam("b_ip")).values(country=bindparam("b_country")), rows)
    return {"ips": len(ips), "with_country": sum(1 for r in rows if r["b_country"])}
