import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..analysis import dns_checks, stats
from ..analysis.stats import Filter
from ..db import get_db
from ..ingest.service import check_and_save
from ..models import AggregateRecord, AggregateReport, Domain, ForensicReport, User
from ..security import current_user, verify_csrf
from ..web import flash, render

router = APIRouter(dependencies=[Depends(current_user)])

PAGE_SIZE = 50


def flt(days: int = Query(30, ge=1, le=3650), domain: str | None = None,
        start: str | None = None, end: str | None = None) -> Filter:
    return Filter.from_params(days, (domain or "").strip().lower() or None, start, end)


def _domains(db: Session) -> list[str]:
    return list(db.scalars(select(Domain.name).order_by(Domain.name)))


def _ctx(db: Session, f: Filter, **kw) -> dict:
    return {"f": f, "domain_names": _domains(db), "days": (f.end - f.start).days + 1, **kw}


@router.get("/")
def dashboard(request: Request, f: Filter = Depends(flt), db: Session = Depends(get_db)):
    data = {
        "summary": stats.summary(db, f),
        "timeline": stats.timeline(db, f),
        "categories": stats.categories(db, f),
        "dispositions": stats.dispositions(db, f),
        "alignment": stats.alignment(db, f),
        "senders": stats.senders(db, f, 12),
        "reporters": stats.reporters(db, f, 10),
        "countries": stats.countries(db, f)[:12],
        "auth": stats.auth_results(db, f),
        "overrides": stats.overrides(db, f),
    }
    return render(request, "dashboard.html", _ctx(
        db, f, data=data, domains=stats.domains_overview(db, f)[:12], insights=stats.insights(db, f)[:10],
        new_sources=stats.new_sources(db, f)[:8]))


@router.get("/domains")
def domains(request: Request, f: Filter = Depends(flt), db: Session = Depends(get_db)):
    return render(request, "domains.html", _ctx(db, f, domains=stats.domains_overview(db, f)))


@router.get("/domains/{name}")
def domain_detail(name: str, request: Request, f: Filter = Depends(flt), db: Session = Depends(get_db)):
    dom = db.scalar(select(Domain).where(Domain.name == name.lower()))
    if not dom:
        raise HTTPException(404, "Doména neexistuje")
    f.domain = dom.name
    data = {
        "summary": stats.summary(db, f),
        "timeline": stats.timeline(db, f),
        "categories": stats.categories(db, f),
        "alignment": stats.alignment(db, f),
        "reporters": stats.reporters(db, f, 10),
    }
    return render(request, "domain_detail.html", _ctx(
        db, f, dom=dom, dns=dom.dns or {}, data=data, readiness=stats.domain_readiness(db, dom, f),
        senders=stats.senders(db, f, 30), failures=stats.failure_details(db, f),
        policy=stats.latest_policy(db, dom.name), new_sources=stats.new_sources(db, f)))


@router.post("/domains/{name}/check", dependencies=[Depends(verify_csrf)])
def domain_check(name: str, request: Request, db: Session = Depends(get_db)):
    dom = db.scalar(select(Domain).where(Domain.name == name.lower()))
    if not dom:
        raise HTTPException(404)
    check_and_save(db, dom)
    db.commit()
    flash(request, f"DNS kontrola {dom.name} dokončená – skóre {dom.dns_score}/100.")
    return RedirectResponse(f"/domains/{dom.name}", 303)


@router.get("/sources")
def sources(request: Request, f: Filter = Depends(flt), category: str | None = None, q: str | None = None,
            db: Session = Depends(get_db)):
    return render(request, "sources.html", _ctx(
        db, f, senders=stats.senders(db, f, 100), rows=stats.sources(db, f, category or None, q),
        category=category, q=q or "", countries=stats.countries(db, f), failures=stats.failure_details(db, f, 20)))


@router.get("/sources.csv")
def sources_csv(f: Filter = Depends(flt), category: str | None = None, q: str | None = None,
                db: Session = Depends(get_db)):
    rows = stats.sources(db, f, category or None, q, limit=100000)
    buf = io.StringIO()
    w = csv.writer(buf)
    cols = ["ip", "ptr", "sender", "country", "asn", "category", "header_from", "total", "pass_pct", "spf_pct",
            "dkim_pct", "enforced", "last_seen"]
    w.writerow(cols)
    for r in rows:
        w.writerow([r[c] for c in cols])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=dmarc-sources.csv"})


@router.get("/reports")
def reports(request: Request, f: Filter = Depends(flt), page: int = Query(1, ge=1), org: str | None = None,
            db: Session = Depends(get_db)):
    q = select(AggregateReport).where(AggregateReport.date_begin >= f.start_dt, AggregateReport.date_begin < f.end_dt)
    if f.domain:
        q = q.where(AggregateReport.domain == f.domain)
    if org:
        q = q.where(AggregateReport.org_name == org)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    items = db.scalars(q.order_by(AggregateReport.date_begin.desc()).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)).all()
    pass_counts = dict(db.execute(
        select(AggregateRecord.report_id, func.sum(AggregateRecord.count))
        .where(AggregateRecord.report_id.in_([r.id for r in items]), AggregateRecord.dmarc_pass.is_(True))
        .group_by(AggregateRecord.report_id)).all()) if items else {}
    return render(request, "reports.html", _ctx(
        db, f, items=items, total=total, page=page, pages=max(1, -(-total // PAGE_SIZE)), org=org or "",
        pass_counts=pass_counts, reporters=stats.reporters(db, f, 30)))


@router.get("/reports/{rid}")
def report_detail(rid: int, request: Request, db: Session = Depends(get_db)):
    rep = db.get(AggregateReport, rid)
    if not rep:
        raise HTTPException(404)
    records = sorted(rep.records, key=lambda r: -r.count)
    return render(request, "report_detail.html", {"rep": rep, "records": records})


@router.get("/reports/{rid}/xml")
def report_xml(rid: int, db: Session = Depends(get_db)):
    rep = db.get(AggregateReport, rid)
    if not rep or not rep.raw_xml:
        raise HTTPException(404)
    return StreamingResponse(iter([rep.raw_xml]), media_type="application/xml",
                             headers={"Content-Disposition": f"attachment; filename=report-{rep.id}.xml"})


@router.get("/forensic")
def forensic(request: Request, f: Filter = Depends(flt), page: int = Query(1, ge=1), db: Session = Depends(get_db)):
    q = select(ForensicReport).where(ForensicReport.arrival_date >= f.start_dt, ForensicReport.arrival_date < f.end_dt)
    if f.domain:
        q = q.where(ForensicReport.reported_domain == f.domain)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    items = db.scalars(q.order_by(ForensicReport.arrival_date.desc()).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)).all()
    return render(request, "forensic.html", _ctx(db, f, items=items, total=total, page=page,
                                                 pages=max(1, -(-total // PAGE_SIZE))))


@router.get("/forensic/{fid}")
def forensic_detail(fid: int, request: Request, db: Session = Depends(get_db)):
    item = db.get(ForensicReport, fid)
    if not item:
        raise HTTPException(404)
    return render(request, "forensic_detail.html", {"item": item})


@router.get("/tls")
def tls(request: Request, f: Filter = Depends(flt), db: Session = Depends(get_db)):
    return render(request, "tls.html", _ctx(db, f, tls=stats.tls_summary(db, f)))


@router.get("/tools")
def tools(request: Request, lookup: str | None = None, db: Session = Depends(get_db)):
    result = None
    if lookup:
        name = lookup.strip().lower().removeprefix("http://").removeprefix("https://").split("/")[0]
        if name and "." in name:
            result = dns_checks.check_domain(name)
    return render(request, "tools.html", {"lookup": lookup or "", "result": result})


@router.get("/profile")
def profile(request: Request, user: User = Depends(current_user)):
    return render(request, "profile.html", {"u": user})


@router.get("/api/dashboard")
def api_dashboard(f: Filter = Depends(flt), db: Session = Depends(get_db)):
    return {
        "filter": {"start": f.start.isoformat(), "end": f.end.isoformat(), "domain": f.domain},
        "summary": stats.summary(db, f),
        "timeline": stats.timeline(db, f),
        "categories": stats.categories(db, f),
        "senders": stats.senders(db, f, 50),
        "domains": stats.domains_overview(db, f),
    }
