"""Aggregation queries powering the dashboard and the domain pages."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import case, distinct, func, select
from sqlalchemy.orm import Session

from ..models import AggregateRecord as R
from ..models import AggregateReport, Domain, ForensicReport, TlsReport
from .senders import CATEGORIES


@dataclass
class Filter:
    start: date
    end: date
    domain: str | None = None

    @classmethod
    def from_params(cls, days: int = 30, domain: str | None = None, start: str | None = None, end: str | None = None):
        today = date.today()
        try:
            s = date.fromisoformat(start) if start else today - timedelta(days=days - 1)
            e = date.fromisoformat(end) if end else today
        except ValueError:
            s, e = today - timedelta(days=days - 1), today
        return cls(start=min(s, e), end=max(s, e), domain=domain or None)

    @property
    def start_dt(self) -> datetime:
        return datetime.combine(self.start, time.min)

    @property
    def end_dt(self) -> datetime:
        """Exclusive upper bound."""
        return datetime.combine(self.end + timedelta(days=1), time.min)

    def previous(self) -> "Filter":
        span = (self.end - self.start).days + 1
        return Filter(self.start - timedelta(days=span), self.start - timedelta(days=1), self.domain)

    def where(self, q):
        q = q.where(R.day >= self.start, R.day <= self.end)
        if self.domain:
            q = q.where(R.domain == self.domain)
        return q


def _sum_if(cond):
    return func.coalesce(func.sum(case((cond, R.count), else_=0)), 0)


TOTAL = func.coalesce(func.sum(R.count), 0)


def _pct(part, whole) -> float | None:
    return round(100.0 * part / whole, 1) if whole else None


# ---------------------------------------------------------------- summary


def summary(db: Session, f: Filter) -> dict:
    def core(flt: Filter):
        row = db.execute(
            flt.where(
                select(
                    TOTAL,
                    _sum_if(R.dmarc_pass),
                    _sum_if(R.spf_aligned),
                    _sum_if(R.dkim_aligned),
                    _sum_if(R.disposition == "quarantine"),
                    _sum_if(R.disposition == "reject"),
                    _sum_if(R.category == "threat"),
                    _sum_if(R.category == "forwarded"),
                    func.count(distinct(R.source_ip)),
                    func.count(distinct(R.report_id)),
                    func.count(distinct(R.org_name)),
                    func.count(distinct(R.domain)),
                )
            )
        ).one()
        return dict(zip(
            ["total", "pass", "spf", "dkim", "quarantine", "reject", "threat", "forwarded", "ips", "reports",
             "reporters", "domains"], [int(x or 0) for x in row], strict=True))

    cur, prev = core(f), core(f.previous())
    cur["pass_pct"] = _pct(cur["pass"], cur["total"])
    cur["spf_pct"] = _pct(cur["spf"], cur["total"])
    cur["dkim_pct"] = _pct(cur["dkim"], cur["total"])
    cur["fail"] = cur["total"] - cur["pass"]
    prev_pct = _pct(prev["pass"], prev["total"])
    cur["pass_delta"] = round(cur["pass_pct"] - prev_pct, 1) if cur["pass_pct"] is not None and prev_pct is not None else None
    cur["total_delta"] = _pct(cur["total"] - prev["total"], prev["total"]) if prev["total"] else None
    fq = select(func.count(ForensicReport.id)).where(
        ForensicReport.arrival_date >= f.start_dt, ForensicReport.arrival_date < f.end_dt
    )
    if f.domain:
        fq = fq.where(ForensicReport.reported_domain == f.domain)
    cur["forensic"] = db.scalar(fq) or 0
    return cur


def timeline(db: Session, f: Filter) -> list[dict]:
    rows = db.execute(
        f.where(
            select(R.day, TOTAL, _sum_if(R.dmarc_pass), _sum_if(R.category == "forwarded"),
                   _sum_if(R.category == "threat"), _sum_if(R.disposition == "quarantine"),
                   _sum_if(R.disposition == "reject"))
        ).group_by(R.day).order_by(R.day)
    ).all()
    by_day = {r[0]: r for r in rows}
    out = []
    d = f.start
    while d <= f.end:
        r = by_day.get(d)
        total, passed = (int(r[1]), int(r[2])) if r else (0, 0)
        out.append({
            "day": d.isoformat(), "total": total, "pass": passed, "fail": total - passed,
            "forwarded": int(r[3]) if r else 0, "threat": int(r[4]) if r else 0,
            "quarantine": int(r[5]) if r else 0, "reject": int(r[6]) if r else 0,
            "pass_pct": _pct(passed, total),
        })
        d += timedelta(days=1)
    return out


def categories(db: Session, f: Filter) -> list[dict]:
    rows = dict(db.execute(f.where(select(R.category, TOTAL)).group_by(R.category)).all())
    return [{"key": k, "label": v, "value": int(rows.get(k, 0))} for k, v in CATEGORIES.items()]


def dispositions(db: Session, f: Filter) -> list[dict]:
    rows = db.execute(f.where(select(R.disposition, TOTAL)).group_by(R.disposition)).all()
    return [{"label": (k or "none"), "value": int(v)} for k, v in rows]


def alignment(db: Session, f: Filter) -> list[dict]:
    row = db.execute(
        f.where(select(
            _sum_if(R.spf_aligned & R.dkim_aligned),
            _sum_if(R.spf_aligned & ~R.dkim_aligned),
            _sum_if(~R.spf_aligned & R.dkim_aligned),
            _sum_if(~R.spf_aligned & ~R.dkim_aligned),
        ))
    ).one()
    labels = ["SPF + DKIM", "Iba SPF", "Iba DKIM", "Žiadne"]
    return [{"label": l, "value": int(v)} for l, v in zip(labels, row, strict=True)]


def auth_results(db: Session, f: Filter) -> dict:
    """Raw (unaligned) SPF / DKIM result distribution."""
    spf, dkim = defaultdict(int), defaultdict(int)
    for spf_res, dkim_res, cnt in db.execute(f.where(select(R.spf_results, R.dkim_results, R.count))):
        spf[(spf_res or [{}])[0].get("result") or "none"] += cnt
        dkim[(dkim_res or [{}])[0].get("result") or "none"] += cnt
    return {"spf": dict(spf), "dkim": dict(dkim)}


def senders(db: Session, f: Filter, limit: int = 50) -> list[dict]:
    label = func.coalesce(R.sender, R.base_domain, "Neznámy")
    rows = db.execute(
        f.where(select(label, R.category, TOTAL, _sum_if(R.dmarc_pass), _sum_if(R.spf_aligned),
                       _sum_if(R.dkim_aligned), func.count(distinct(R.source_ip))))
        .group_by(label, R.category)
    ).all()
    agg: dict = {}
    for name, cat, total, passed, spf, dkim, ips in rows:
        a = agg.setdefault(name, {"name": name, "total": 0, "pass": 0, "spf": 0, "dkim": 0, "ips": 0, "cats": defaultdict(int)})
        a["total"] += total
        a["pass"] += passed
        a["spf"] += spf
        a["dkim"] += dkim
        a["ips"] += ips
        a["cats"][cat] += total
    out = sorted(agg.values(), key=lambda a: a["total"], reverse=True)[:limit]
    for a in out:
        a["category"] = max(a["cats"], key=a["cats"].get)
        a["cats"] = dict(a["cats"])
        a["pass_pct"] = _pct(a["pass"], a["total"])
        a["spf_pct"] = _pct(a["spf"], a["total"])
        a["dkim_pct"] = _pct(a["dkim"], a["total"])
        a["known"] = a["name"] != "Neznámy"
    return out


def reporters(db: Session, f: Filter, limit: int = 15) -> list[dict]:
    rows = db.execute(
        f.where(select(R.org_name, TOTAL, _sum_if(R.dmarc_pass), func.count(distinct(R.report_id))))
        .group_by(R.org_name).order_by(TOTAL.desc()).limit(limit)
    ).all()
    return [{"name": n, "total": int(t), "pass_pct": _pct(p, t), "reports": int(c)} for n, t, p, c in rows]


def countries(db: Session, f: Filter) -> list[dict]:
    rows = db.execute(
        f.where(select(func.coalesce(R.country, "Neznáma"), TOTAL, _sum_if(R.dmarc_pass)))
        .group_by(func.coalesce(R.country, "Neznáma")).order_by(TOTAL.desc())
    ).all()
    return [{"name": n, "total": int(t), "fail": int(t - p)} for n, t, p in rows]


def sources(db: Session, f: Filter, category: str | None = None, q: str | None = None, limit: int = 500) -> list[dict]:
    stmt = select(
        R.source_ip, R.ptr, R.sender, R.country, R.asn_org, R.category, R.header_from,
        TOTAL, _sum_if(R.dmarc_pass), _sum_if(R.spf_aligned), _sum_if(R.dkim_aligned),
        _sum_if(R.disposition != "none"), func.max(R.day),
    )
    stmt = f.where(stmt)
    if category:
        stmt = stmt.where(R.category == category)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(R.source_ip.like(like) | R.ptr.like(like) | func.lower(R.sender).like(like)
                          | R.header_from.like(like))
    rows = db.execute(
        stmt.group_by(R.source_ip, R.ptr, R.sender, R.country, R.asn_org, R.category, R.header_from)
        .order_by(TOTAL.desc()).limit(limit)
    ).all()
    out = []
    for ip, ptr, sender, country, asn, cat, hf, total, passed, spf, dkim, enforced, last in rows:
        out.append({
            "ip": ip, "ptr": ptr, "sender": sender or "Neznámy", "country": country, "asn": asn,
            "category": cat, "header_from": hf, "total": int(total), "pass_pct": _pct(passed, total),
            "spf_pct": _pct(spf, total), "dkim_pct": _pct(dkim, total), "enforced": int(enforced),
            "last_seen": last.isoformat() if last else None,
        })
    return out


def failure_details(db: Session, f: Filter, limit: int = 15) -> list[dict]:
    """Most frequent DKIM/SPF failure combinations among failing traffic."""
    agg: dict = defaultdict(int)
    stmt = f.where(select(R.dkim_results, R.spf_results, R.count, R.sender).where(R.dmarc_pass.is_(False)))
    for dkim, spf, cnt, sender in db.execute(stmt):
        d = (dkim or [{}])[0]
        s = (spf or [{}])[0]
        key = (sender or "Neznámy", f"{d.get('domain') or '-'} ({d.get('result') or 'none'})",
               f"{s.get('domain') or '-'} ({s.get('result') or 'none'})")
        agg[key] += cnt
    top = sorted(agg.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [{"sender": k[0], "dkim": k[1], "spf": k[2], "count": v} for k, v in top]


def overrides(db: Session, f: Filter) -> list[dict]:
    agg: dict = defaultdict(int)
    for reasons, cnt in db.execute(f.where(select(R.reasons, R.count)).where(R.reasons.is_not(None))):
        for r in reasons or []:
            agg[r.get("type") or "unknown"] += cnt
    return [{"label": k, "value": v} for k, v in sorted(agg.items(), key=lambda kv: -kv[1])]


# ---------------------------------------------------------------- domains


def latest_policy(db: Session, domain: str) -> dict | None:
    rep = db.scalar(
        select(AggregateReport).where(AggregateReport.domain == domain).order_by(AggregateReport.date_end.desc()).limit(1)
    )
    if not rep:
        return None
    return {"p": rep.p, "sp": rep.sp, "pct": rep.pct, "adkim": rep.adkim, "aspf": rep.aspf, "fo": rep.fo}


def domains_overview(db: Session, f: Filter) -> list[dict]:
    rows = db.execute(
        Filter(f.start, f.end).where(
            select(R.domain, TOTAL, _sum_if(R.dmarc_pass), _sum_if(R.category == "threat"),
                   _sum_if(R.category == "forwarded"), func.count(distinct(R.source_ip)))
        ).group_by(R.domain)
    ).all()
    stats = {r[0]: r for r in rows}
    spark_rows = db.execute(
        Filter(f.start, f.end).where(select(R.domain, R.day, TOTAL)).group_by(R.domain, R.day)
    ).all()
    sparks: dict = defaultdict(dict)
    for dom, day, total in spark_rows:
        sparks[dom][day.isoformat()] = int(total)
    days = [(f.start + timedelta(days=i)).isoformat() for i in range((f.end - f.start).days + 1)]
    out = []
    for dom in db.scalars(select(Domain).order_by(Domain.name)):
        s = stats.get(dom.name)
        total, passed, threat, fwd, ips = (int(x) for x in s[1:]) if s else (0, 0, 0, 0, 0)
        dmarc = (dom.dns or {}).get("dmarc", {})
        out.append({
            "name": dom.name,
            "total": total,
            "pass_pct": _pct(passed, total),
            "threat": threat,
            "forwarded": fwd,
            "ips": ips,
            "policy": (dmarc.get("tags") or {}).get("p") or (latest_policy(db, dom.name) or {}).get("p"),
            "has_dmarc": bool(dmarc.get("record")),
            "score": dom.dns_score,
            "grade": (dom.dns or {}).get("grade"),
            "last_seen": dom.last_seen.isoformat() if dom.last_seen else None,
            "spark": [sparks[dom.name].get(d, 0) for d in days],
        })
    out.sort(key=lambda d: d["total"], reverse=True)
    return out


# --------------------------------------------------------------- insights


def new_sources(db: Session, f: Filter, days: int = 7) -> list[dict]:
    """Failing source IPs first seen within the last `days` of the period."""
    cutoff = f.end - timedelta(days=days - 1)
    stmt = select(R.source_ip, func.min(R.day), TOTAL, func.max(R.sender), func.max(R.ptr), func.max(R.domain)).where(
        R.dmarc_pass.is_(False)
    )
    if f.domain:
        stmt = stmt.where(R.domain == f.domain)
    rows = db.execute(stmt.group_by(R.source_ip).having(func.min(R.day) >= cutoff).order_by(TOTAL.desc()).limit(20)).all()
    return [{"ip": ip, "first": first.isoformat(), "total": int(t), "sender": s, "ptr": p, "domain": d}
            for ip, first, t, s, p, d in rows]


def domain_readiness(db: Session, dom: Domain, f: Filter) -> dict:
    """Assess whether a domain can safely move to a stricter DMARC policy."""
    df = Filter(f.start, f.end, dom.name)
    s = summary(db, df)
    cats = {c["key"]: c["value"] for c in categories(db, df)}
    legit = cats["compliant"] + cats["noncompliant"]
    compliance = _pct(cats["compliant"], legit)
    dns = dom.dns or {}
    tags = (dns.get("dmarc") or {}).get("tags") or {}
    policy = (tags.get("p") or (latest_policy(db, dom.name) or {}).get("p") or "").lower()
    days_with_data = db.scalar(select(func.count(distinct(R.day))).where(R.domain == dom.name, R.day >= f.start)) or 0
    recs: list[dict] = []
    level = "ok"

    if not (dns.get("dmarc") or {}).get("record"):
        recs.append({"level": "error", "msg": f"Publikujte DMARC záznam: _dmarc.{dom.name} TXT \"v=DMARC1; p=none; rua=mailto:...\""})
        level = "error"
    if s["total"] == 0:
        recs.append({"level": "info", "msg": "V zvolenom období nie sú žiadne agregované dáta."})
    elif policy == "none":
        if compliance is not None and compliance >= 98 and days_with_data >= 14:
            recs.append({"level": "ok", "msg": f"Legitímna pošta je v {compliance}% súlade – môžete prejsť na p=quarantine (napr. pct=25 → 100)."})
        else:
            recs.append({"level": "warn", "msg": "Pred sprísnením politiky opravte SPF/DKIM pri legitímnych odosielateľoch (pozri Nesúlad)."})
            level = "warn"
        if cats["threat"]:
            recs.append({"level": "warn", "msg": f"{cats['threat']:,} správ z neautorizovaných zdrojov bolo doručených, pretože politika je p=none.".replace(",", " ")})
            level = "warn" if level == "ok" else level
    elif policy == "quarantine":
        if compliance is not None and compliance >= 99:
            recs.append({"level": "ok", "msg": f"Súlad {compliance}% – doména je pripravená na p=reject."})
        else:
            recs.append({"level": "warn", "msg": "Pred p=reject vyriešte nesúladných legitímnych odosielateľov."})
            level = "warn"
    elif policy == "reject":
        recs.append({"level": "ok", "msg": "Doména má najprísnejšiu politiku p=reject."})
    if cats["noncompliant"]:
        recs.append({"level": "warn", "msg": f"{cats['noncompliant']:,} správ od známych služieb neprešlo DMARC – nastavte pre ne DKIM so zarovnanou doménou.".replace(",", " ")})
        level = "warn" if level == "ok" else level
    for key in ("spf", "dkim", "mta_sts"):
        for issue in (dns.get(key) or {}).get("issues", []):
            if issue["level"] == "error":
                recs.append({"level": "error", "msg": f"{key.upper().replace('_', '-')}: {issue['msg']}"})
                level = "error"
    return {"policy": policy or None, "compliance": compliance, "days_with_data": days_with_data,
            "level": level, "recommendations": recs, "categories": cats}


def insights(db: Session, f: Filter) -> list[dict]:
    out = []
    doms = db.scalars(select(Domain).where(Domain.name == f.domain)) if f.domain else db.scalars(select(Domain))
    for dom in doms:
        r = domain_readiness(db, dom, f)
        for rec in r["recommendations"]:
            if rec["level"] in ("error", "warn") or "pripravená" in rec["msg"] or "môžete prejsť" in rec["msg"]:
                out.append({**rec, "domain": dom.name})
    order = {"error": 0, "warn": 1, "ok": 2, "info": 3}
    out.sort(key=lambda r: order.get(r["level"], 9))
    return out[:30]


def tls_summary(db: Session, f: Filter) -> dict:
    q = select(TlsReport).where(TlsReport.date_begin >= f.start_dt, TlsReport.date_begin < f.end_dt)
    if f.domain:
        q = q.where(TlsReport.policy_domain == f.domain)
    reports = list(db.scalars(q.order_by(TlsReport.date_begin.desc())))
    success = sum(r.success_count for r in reports)
    failure = sum(r.failure_count for r in reports)
    by_type: dict = defaultdict(int)
    for r in reports:
        for fd in r.failure_details or []:
            by_type[fd.get("result-type", "unknown")] += int(fd.get("failed-session-count", 0))
    return {"reports": reports, "success": success, "failure": failure,
            "success_pct": _pct(success, success + failure), "failure_types": dict(by_type)}
