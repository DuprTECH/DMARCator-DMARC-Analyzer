"""Persist parsed reports, run mailbox sources and DNS refreshes."""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..analysis import dns_checks
from ..analysis.cache import invalidate
from ..analysis.enrich import enrich_ips
from ..analysis.senders import categorize, identify
from ..config import get_settings
from ..db import session_scope
from ..models import (
    AggregateRecord,
    AggregateReport,
    Domain,
    ForensicReport,
    IngestLog,
    MailSource,
    TlsReport,
    utcnow,
)
from ..security import decrypt_json
from .graph import GraphMailbox
from .imap import ImapMailbox
from .parser import ExtractResult, ParsedReport, extract_from_email

log = logging.getLogger(__name__)
_locks: dict[int, threading.Lock] = {}
_dns_lock = threading.Lock()


def add_log(db: Session, source: str, message: str, level: str = "info"):
    db.add(IngestLog(source=source, message=message[:4000], level=level))
    getattr(log, "warning" if level == "error" else "info")("[%s] %s", source, message)


def _touch_domain(db: Session, name: str, seen):
    if not name:
        return
    dom = db.scalar(select(Domain).where(Domain.name == name))
    if not dom:
        dom = Domain(name=name, last_seen=seen)
        db.add(dom)
        db.flush()
    elif not dom.last_seen or (seen and seen > dom.last_seen):
        dom.last_seen = seen


# ------------------------------------------------------------------ store


def first_results(dkim: list | None, spf: list | None) -> dict:
    d = (dkim or [{}])[0]
    s = (spf or [{}])[0]
    return {"dkim_domain": d.get("domain") or None, "dkim_result": d.get("result") or None,
            "spf_domain": s.get("domain") or None, "spf_result": s.get("result") or None}


def store(db: Session, parsed: ParsedReport, source: str) -> bool:
    """Persist one parsed report. Returns False for duplicates."""
    d = parsed.data
    if parsed.kind == "aggregate":
        exists = db.scalar(
            select(AggregateReport.id).where(
                AggregateReport.org_name == d["org_name"],
                AggregateReport.report_id == d["report_id"],
                AggregateReport.domain == d["domain"],
            )
        )
        if exists:
            return False
        rep = AggregateReport(
            **{k: d[k] for k in ("org_name", "org_email", "report_id", "domain", "date_begin", "date_end",
                                 "adkim", "aspf", "p", "sp", "np", "pct", "fo", "message_count")},
            source=source,
            raw_xml=parsed.raw,
        )
        db.add(rep)
        db.flush()
        infos = enrich_ips(db, {r["source_ip"] for r in d["records"]})
        for r in d["records"]:
            info = infos.get(r["source_ip"])
            ptr = info.ptr if info else None
            sender = identify(
                ptr,
                [x["domain"] for x in r["dkim_results"] if x.get("result") == "pass"],
                [x["domain"] for x in r["spf_results"] if x.get("result") == "pass"],
                info.asn_org if info else None,
            )
            db.add(
                AggregateRecord(
                    report_id=rep.id,
                    domain=d["domain"],
                    day=d["date_begin"].date(),
                    org_name=d["org_name"],
                    **{k: r[k] for k in ("source_ip", "count", "disposition", "policy_dkim", "policy_spf",
                                         "dmarc_pass", "spf_aligned", "dkim_aligned", "reasons", "header_from",
                                         "envelope_from", "envelope_to", "dkim_results", "spf_results")},
                    **first_results(r["dkim_results"], r["spf_results"]),
                    ptr=ptr,
                    base_domain=info.base_domain if info else None,
                    sender=sender,
                    category=categorize(r, sender),
                    country=info.country if info else None,
                    asn_org=info.asn_org if info else None,
                )
            )
        _touch_domain(db, d["domain"], d["date_end"])
        return True

    if parsed.kind == "forensic":
        if db.scalar(select(ForensicReport.id).where(ForensicReport.fingerprint == d["fingerprint"])):
            return False
        fr = ForensicReport(**d, source=source)
        if d.get("source_ip"):
            info = enrich_ips(db, {d["source_ip"]}).get(d["source_ip"])
            if info:
                fr.ptr, fr.country = info.ptr, info.country
        db.add(fr)
        _touch_domain(db, d.get("reported_domain"), d.get("arrival_date") or utcnow())
        return True

    if parsed.kind == "tls":
        added = False
        for p in d["policies"]:
            if db.scalar(
                select(TlsReport.id).where(
                    TlsReport.org_name == d["org_name"],
                    TlsReport.report_id == d["report_id"],
                    TlsReport.policy_domain == p["policy_domain"],
                )
            ):
                continue
            db.add(TlsReport(org_name=d["org_name"], report_id=d["report_id"], contact=d.get("contact"),
                             date_begin=d["date_begin"], date_end=d["date_end"], source=source, **p))
            _touch_domain(db, p["policy_domain"], d["date_end"])
            added = True
        return added
    return False


def store_result(db: Session, result: ExtractResult, source: str) -> dict:
    stats = {"new": 0, "duplicate": 0, "errors": list(result.errors)}
    for parsed in result.reports:
        try:
            with db.begin_nested():
                if store(db, parsed, source):
                    stats["new"] += 1
                else:
                    stats["duplicate"] += 1
        except Exception as exc:  # noqa: BLE001
            log.exception("store failed")
            stats["errors"].append(f"{parsed.kind}: {exc}")
    if stats["new"]:
        invalidate()
    return stats


# ----------------------------------------------------------- mail sources


def open_mailbox(source: MailSource):
    cfg = decrypt_json(source.config_encrypted)
    if source.kind == "graph":
        return GraphMailbox(cfg)
    if source.kind == "imap":
        return ImapMailbox(cfg)
    raise ValueError(f"Neznámy typ zdroja {source.kind}")


def run_source(source_id: int, limit: int = 500) -> str:
    lock = _locks.setdefault(source_id, threading.Lock())
    if not lock.acquire(blocking=False):
        return "Zdroj sa práve spracúva."
    try:
        with session_scope() as db:
            src = db.get(MailSource, source_id)
            if not src:
                return "Zdroj neexistuje."
            name = src.name
            mbox = open_mailbox(src)
        totals = {"messages": 0, "new": 0, "duplicate": 0, "errors": 0}
        status, message = "ok", ""
        try:
            for mid, subject, raw in mbox.fetch(limit=limit):
                totals["messages"] += 1
                if len(raw) > get_settings().max_attachment_mb * 1024 * 1024:
                    with session_scope() as db:
                        add_log(db, name, f"Správa '{subject}' prekročila limit veľkosti", "error")
                    mbox.done(mid, False)
                    continue
                with session_scope() as db:
                    res = extract_from_email(raw)
                    st = store_result(db, res, name)
                    for e in st["errors"]:
                        add_log(db, name, f"'{subject[:120]}': {e}", "error")
                    if not res.reports and not res.errors:
                        add_log(db, name, f"'{subject[:120]}': správa neobsahuje DMARC/TLS report", "warn")
                ok = not st["errors"]
                totals["new"] += st["new"]
                totals["duplicate"] += st["duplicate"]
                totals["errors"] += 0 if ok else 1
                mbox.done(mid, ok or bool(res.reports))
            message = (f"{totals['messages']} správ, {totals['new']} nových reportov, "
                       f"{totals['duplicate']} duplicít, {totals['errors']} chýb")
            if totals["errors"]:
                status = "warn"
        except Exception as exc:  # noqa: BLE001
            log.exception("source %s failed", name)
            status, message = "error", f"{exc.__class__.__name__}: {exc}"
        finally:
            mbox.close()
        with session_scope() as db:
            src = db.get(MailSource, source_id)
            src.last_run, src.last_status, src.last_message = utcnow(), status, message
            add_log(db, name, message, "error" if status == "error" else "info")
        if totals["new"]:
            refresh_dns(only_missing=True)
        return message
    finally:
        lock.release()


def run_all_sources():
    with session_scope() as db:
        ids = list(db.scalars(select(MailSource.id).where(MailSource.enabled.is_(True))))
    for sid in ids:
        run_source(sid)


# ------------------------------------------------------------------- DNS


def selectors_for(db: Session, domain: str) -> list[str]:
    since = utcnow().date() - timedelta(days=90)
    rows = db.scalars(
        select(AggregateRecord.dkim_results)
        .where(AggregateRecord.domain == domain, AggregateRecord.day >= since)
        .order_by(AggregateRecord.id.desc())
        .limit(5000)
    )
    found: dict[str, int] = {}
    for results in rows:
        for r in results or []:
            dom = r.get("domain") or ""
            if r.get("selector") and dom == domain:
                found[r["selector"]] = found.get(r["selector"], 0) + 1
    return sorted(found, key=found.get, reverse=True)[:10]


def check_and_save(db: Session, dom: Domain) -> dict:
    result = dns_checks.check_domain(dom.name, selectors_for(db, dom.name))
    dom.dns, dom.dns_score, dom.dns_checked_at = result, result["score"], utcnow()
    return result


def refresh_dns(only_missing: bool = False):
    if not _dns_lock.acquire(blocking=False):
        return
    try:
        with session_scope() as db:
            q = select(Domain.id, Domain.name)
            if only_missing:
                q = q.where(Domain.dns_checked_at.is_(None))
            todo = [(did, name, selectors_for(db, name)) for did, name in db.execute(q)]

        def check(item):
            did, name, selectors = item
            try:
                return did, dns_checks.check_domain(name, selectors)
            except Exception:  # noqa: BLE001
                log.exception("DNS check failed for %s", name)
                return did, None

        # network-bound: check several domains at once, write results from this thread
        with ThreadPoolExecutor(max_workers=8) as pool:
            for did, result in pool.map(check, todo):
                if result:
                    with session_scope() as db:
                        dom = db.get(Domain, did)
                        if dom:
                            dom.dns, dom.dns_score, dom.dns_checked_at = result, result["score"], utcnow()
        invalidate()
        log.info("DNS check finished for %d domains", len(todo))
    finally:
        _dns_lock.release()
