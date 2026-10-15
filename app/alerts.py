"""E-mail alerts: an immediate notice about serious changes and a monthly summary.

Serious changes are detected per domain against its own recent history (median of the previous
28 days), so a domain with thousands of failing forwards a day doesn't raise alarms while a quiet
domain that suddenly fails does:

* fail_spike       – DMARC failures not explained by forwarding far above the usual level
* compliance_drop  – DMARC compliance of a day far below the usual level
* dns_removed      – a published DMARC / SPF record or all DKIM keys disappeared (from the DNS check)
* mailbox          – a report mailbox fails

Every event is stored once in the `alerts` table (unique key) and e-mailed by Microsoft Graph or SMTP.
"""

import logging
import smtplib
import ssl
import statistics
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from html import escape

from sqlalchemy import select
from sqlalchemy.orm import Session

from .analysis import stats
from . import email_render as er
from .analysis.stats import TOTAL, Filter, _sum_if
from .db import session_scope
from .i18n import set_language, translate
from .models import AggregateRecord as R
from .models import Alert, AppSetting, Domain, MailSource, utcnow
from .security import decrypt_json, encrypt_json

log = logging.getLogger(__name__)

SETTINGS_KEY = "alerts"
DEFAULTS = {
    "enabled": False, "instant": True, "monthly": True, "recipients": "", "transport": "graph",
    "graph_source_id": "", "smtp_host": "", "smtp_port": "587", "smtp_security": "starttls",
    "smtp_username": "", "smtp_password": "", "smtp_from": "", "min_failures": "50",
}
BOOL_FIELDS = ("enabled", "instant", "monthly")
SECRET_FIELDS = ("smtp_password",)
BASELINE_DAYS = 28


# ------------------------------------------------------------------ settings


def load() -> dict:
    with session_scope() as db:
        row = db.get(AppSetting, SETTINGS_KEY)
        stored = decrypt_json(row.value) if row else {}
    return {**DEFAULTS, **stored}


def save(form: dict, username: str) -> dict:
    current = load()
    cfg = {k: str(form.get(k) or "").strip() for k in DEFAULTS if k not in BOOL_FIELDS}
    cfg.update({k: form.get(k) == "on" for k in BOOL_FIELDS})
    for k in SECRET_FIELDS:  # an empty secret field keeps the stored value
        cfg[k] = cfg[k] or current.get(k, "")
    if cfg["transport"] not in ("graph", "smtp"):
        raise ValueError("Neplatný spôsob odosielania.")
    if cfg["enabled"] and not recipients(cfg):
        raise ValueError("Zadajte aspoň jedného príjemcu.")
    if not cfg["min_failures"].isdecimal():
        raise ValueError("Minimálny počet zlyhaní musí byť celé číslo.")
    with session_scope() as db:
        row = db.get(AppSetting, SETTINGS_KEY) or AppSetting(key=SETTINGS_KEY)
        row.value, row.updated_by = encrypt_json(cfg), username
        db.add(row)
    return cfg


def recipients(cfg: dict) -> list[str]:
    return [a.strip() for a in cfg.get("recipients", "").replace(";", ",").split(",") if "@" in a]


# ------------------------------------------------------------------ sending


def send(cfg: dict, subject: str, html: str) -> None:
    """Send one HTML e-mail to the configured recipients (raises on failure)."""
    to = recipients(cfg)
    if not to:
        raise ValueError("Nie sú nastavení príjemcovia.")
    if cfg["transport"] == "graph":
        from .ingest.graph import GraphMailbox

        with session_scope() as db:
            src = db.get(MailSource, int(cfg["graph_source_id"] or 0)) if cfg["graph_source_id"] else None
            if not src or src.kind != "graph":
                raise ValueError("Vyberte schránku Exchange Online, cez ktorú sa budú upozornenia posielať.")
            box_cfg = decrypt_json(src.config_encrypted)
        box = GraphMailbox(box_cfg)
        try:
            box._req("POST", "/sendMail", json={"saveToSentItems": True, "message": {
                "subject": subject, "body": {"contentType": "HTML", "content": html},
                "toRecipients": [{"emailAddress": {"address": a}} for a in to]}})
        finally:
            box.close()
        return
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, cfg["smtp_from"] or cfg["smtp_username"], ", ".join(to)
    msg.set_content(translate("Tento e-mail vyžaduje zobrazenie HTML."))
    msg.add_alternative(html, subtype="html")
    port = int(cfg["smtp_port"] or 0) or (465 if cfg["smtp_security"] == "ssl" else 587)
    ctx = ssl.create_default_context()
    if cfg["smtp_security"] == "ssl":
        server = smtplib.SMTP_SSL(cfg["smtp_host"], port, timeout=30, context=ctx)
    else:
        server = smtplib.SMTP(cfg["smtp_host"], port, timeout=30)
    with server:
        if cfg["smtp_security"] == "starttls":
            server.starttls(context=ctx)
        if cfg["smtp_username"]:
            server.login(cfg["smtp_username"], cfg["smtp_password"])
        server.send_message(msg)


def _language() -> None:
    """E-mails use the shared default language from Time and language settings."""
    from .settings_store import effective_settings

    s = effective_settings()
    set_language(s.display_language if s.display_language in ("sk", "en") else s.default_language)


def _page(title: str, subtitle: str, body: str) -> str:
    from .settings_store import effective_settings

    s = effective_settings()
    footer = translate("Automatický e-mail – nastavenia v Administrácia → Upozornenia e-mailom")
    return er.page(title, subtitle, body, s.app_name, s.base_url, escape(footer))


# event kind → colour of its marker / badge
KIND_STATUS = {"fail_spike": "critical", "dns_removed": "critical", "mailbox": "serious", "compliance_drop": "warning"}
KIND_LABEL = {"fail_spike": "Nárast zlyhaní", "dns_removed": "Zmiznutý DNS záznam", "mailbox": "Schránka",
              "compliance_drop": "Pokles súladu"}


# ------------------------------------------------------------------ detection


def record(db: Session, key: str, kind: str, domain: str | None, message: str) -> bool:
    """Store an event once; returns False when it is already known."""
    if db.scalar(select(Alert.id).where(Alert.key == key)):
        return False
    db.add(Alert(key=key, kind=kind, domain=domain, message=message))
    db.flush()
    return True


def dns_regressions(db: Session, domain: str, old: dict | None, new: dict) -> None:
    """Called after a DNS check: a record that was published before and is gone now."""
    if not old:
        return
    today = date.today().isoformat()
    for key, label in (("dmarc", "DMARC"), ("spf", "SPF"), ("dkim", "DKIM")):
        before, after = old.get(key) or {}, new.get(key) or {}
        if "unknown" in (before.get("status"), after.get("status")):
            continue  # the DNS server didn't answer – not a proof of removal
        had = bool(before.get("selectors")) if key == "dkim" else bool(before.get("record"))
        has = bool(after.get("selectors")) if key == "dkim" else bool(after.get("record"))
        if had and not has:
            record(db, f"dns_removed:{key}:{domain}:{today}", "dns_removed", domain,
                   f"{domain}: záznam {label} zmizol z DNS")


def _median_mad(values: list[float]) -> tuple[float, float]:
    med = statistics.median(values)
    return med, statistics.median(abs(v - med) for v in values)


def traffic_anomalies(db: Session, today: date | None = None, min_failures: int = 50) -> int:
    """Compare the last two days of every monitored domain with its previous 28 days."""
    today = today or date.today()
    start = today - timedelta(days=BASELINE_DAYS + 2)
    rows = db.execute(
        Filter(start, today).where(select(R.domain, R.day, TOTAL, _sum_if(R.dmarc_pass),
                                          _sum_if(R.category.in_(("noncompliant", "threat")))))
        .where(R.domain.in_(select(Domain.name).where(stats.not_ignored()))).group_by(R.domain, R.day)
    ).all()
    per: dict[str, dict[date, tuple[int, int, int]]] = {}
    for dom, day, total, passed, failed in rows:
        per.setdefault(dom, {})[day] = (int(total), int(passed), int(failed))
    added = 0
    for dom, days in per.items():
        for d in (today - timedelta(days=1), today - timedelta(days=2)):
            if d not in days:
                continue
            total, passed, failed = days[d]
            history = [days.get(d - timedelta(days=i), (0, 0, 0)) for i in range(1, BASELINE_DAYS + 1)]
            med, mad = _median_mad([h[2] for h in history])
            limit = max(min_failures, 3 * med, med + 6 * 1.4826 * mad)
            if failed > limit:
                added += record(db, f"fail_spike:{dom}:{d}", "fail_spike", dom,
                                f"{dom}: {failed} zlyhaní DMARC za {d.isoformat()} (bežne okolo {round(med)} denne)")
            usual = [100 * h[1] / h[0] for h in history if h[0] >= 20]
            if total >= 100 and len(usual) >= 7:
                base = statistics.median(usual)
                pct = 100 * passed / total
                if pct < base - 20:
                    added += record(db, f"compliance_drop:{dom}:{d}", "compliance_drop", dom,
                                    f"{dom}: súlad DMARC {pct:.0f} % za {d.isoformat()} (bežne {base:.0f} %)")
    return added


def mailbox_problems(db: Session) -> int:
    added = 0
    today = date.today().isoformat()
    for src in db.scalars(select(MailSource).where(MailSource.enabled.is_(True))):
        if src.last_status == "error":
            added += record(db, f"mailbox:{src.id}:{today}", "mailbox", None,
                            f"Schránka {src.name} nefunguje: {(src.last_message or '')[:300]}")
    return added


def hourly() -> None:
    """Scheduler job: serious events right away, the monthly summary in the first days of a month
    (from 07:00, so a server that was down on the 1st still sends it)."""
    check_and_notify()
    now = datetime.now()
    if now.day <= 7 and now.hour >= 7:
        try:
            send_monthly()
        except Exception as exc:  # noqa: BLE001 – retried next hour
            log.warning("monthly report failed: %s", exc)


def check_and_notify() -> int:
    """Detect new serious events and e-mail everything not sent yet. Returns the number e-mailed."""
    cfg = load()
    with session_scope() as db:
        traffic_anomalies(db, min_failures=int(cfg["min_failures"] or 50))
        mailbox_problems(db)
    if not (cfg["enabled"] and cfg["instant"]):
        return 0
    with session_scope() as db:
        pending = list(db.scalars(select(Alert).where(Alert.sent_at.is_(None), Alert.kind != "monthly")
                                  .order_by(Alert.created_at)))
        if not pending:
            return 0
        _language()
        counts = {k: sum(a.kind == k for a in pending) for k in KIND_LABEL}
        tiles = [er.kpi(translate(label), str(counts[k]), status=KIND_STATUS[k] if counts[k] else None)
                 for k, label in KIND_LABEL.items()]
        body = er.kpi_row(tiles) + er.section(translate("Čo sa stalo"), er.event_list(
            [(KIND_STATUS.get(a.kind, "info"), translate(a.message)) for a in pending]))
        html = _page(translate("Vážne zmeny v DMARC"),
                     translate("{n} nových udalostí", n=len(pending)), body)
        try:
            send(cfg, f"[DMARCator] {translate('Upozornenie')}: {translate(pending[0].message)}"
                 + (f" (+{len(pending) - 1})" if len(pending) > 1 else ""), html)
        except Exception as exc:  # noqa: BLE001 – keep them pending, retry next hour
            log.warning("alert e-mail failed: %s", exc)
            for a in pending:
                a.error = str(exc)[:1000]
            return 0
        for a in pending:
            a.sent_at, a.error = utcnow(), None
        return len(pending)


# ------------------------------------------------------------------ monthly report


def monthly_html(db: Session, month: date) -> tuple[str, str]:
    """Subject and HTML of the summary for the calendar month containing `month`."""
    tr = translate
    first = month.replace(day=1)
    last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    f = Filter(first, last)
    s = stats.summary(db, f)

    def delta(value, unit):
        if value is None:
            return tr("bez porovnania")
        arrow, status = ("▲", "good") if value > 0 else ("▼", "critical") if value < 0 else ("●", "neutral")
        if unit == "%":  # message volume – a change is neither good nor bad
            status = "neutral"
        return f'<span style="color:{er.STATUS[status][1]}">{arrow} {abs(value):.1f} {unit}</span> ' + tr("oproti minulému mesiacu")

    tiles = [
        er.kpi(tr("Súlad DMARC"), f"{s['pass_pct'] or 0:.1f}&nbsp;%", delta(s["pass_delta"], "pp"), er.rate_status(s["pass_pct"])),
        er.kpi(tr("Správ"), er.n(s["total"]), delta(s["total_delta"], "%")),
        er.kpi(tr("Zlyhalo DMARC"), er.n(s["fail"]), tr("z toho preposlané {n}", n=er.n(s["forwarded"]))),
        er.kpi(tr("Hrozby"), er.n(s["threat"]), tr("neznáme zdroje"), "critical" if s["threat"] else "good"),
    ]

    policy_status = {"reject": "good", "quarantine": "warning", "none": "critical"}
    grade_status = {"A": "good", "B": "good", "C": "warning", "D": "serious", "F": "critical"}
    domains = [d for d in stats.domains_overview(db, f) if not d["ignored"] and d["total"]]
    rows = [[
        f"<strong>{escape(d['name'])}</strong>",
        er.n(d["total"]),
        er.bar(d["pass_pct"]),
        f'<span style="color:{er.STATUS["critical"][1]};font-weight:600">{er.n(d["threat"])}</span>' if d["threat"] else "0",
        er.badge(f"p={d['policy']}" if d["policy"] else tr("bez DMARC"), policy_status.get(d["policy"], "critical")),
        er.badge(d["grade"] or "?", grade_status.get(d["grade"], "neutral")),
    ] for d in domains[:40]]
    more = len(domains) - 40
    domain_table = er.table([tr("Doména"), tr("Správ"), tr("Súlad DMARC"), tr("Hrozby"), tr("Politika"), "DNS"], rows,
                            ["left", "right", "left", "right", "left", "center"])
    if more > 0:
        domain_table += f'<div style="padding:8px 12px;font-size:12px;color:{er.MUTED}">{tr("a ďalších {n} domén", n=more)}</div>'

    posture = stats.dns_posture(db)
    dns_rows = [[
        f"<strong>{c['name']}</strong>",
        er.badge(f"OK {c['ok']}", "good"),
        er.badge(f"{tr('Upozornenie')} {c['warn']}", "warning") if c["warn"] else "",
        er.badge(f"{tr('Chyba')} {c['error']}", "critical") if c["error"] else "",
        er.badge(f"{tr('Chýba')} {c['missing']}", "critical" if c["key"] in ("dmarc", "spf", "dkim") else "neutral")
        if c["missing"] else "",
    ] for c in posture["checks"]]

    events = list(db.scalars(select(Alert).where(
        Alert.kind != "monthly", Alert.created_at >= datetime.combine(first, datetime.min.time()),
        Alert.created_at < datetime.combine(last + timedelta(days=1), datetime.min.time())).order_by(Alert.created_at)))
    event_html = (er.event_list([(KIND_STATUS.get(a.kind, "info"), tr(a.message)) for a in events]) if events else
                  er.event_list([("good", tr("Žiadne vážne zmeny"))]))

    body = (er.kpi_row(tiles)
            + er.section(tr("Domény"), domain_table)
            + er.section(tr("DNS záznamy všetkých domén"), er.table(["", "", "", "", ""], dns_rows))
            + er.section(tr("Upozornenia za mesiac"), event_html))
    title = f"{tr('Mesačný DMARC report')} {first.strftime('%Y-%m')}"
    return f"[DMARCator] {title}", _page(title, f"{first.isoformat()} – {last.isoformat()}", body)


def send_monthly(month: date | None = None, force: bool = False) -> bool:
    """Send the summary of the previous calendar month (once per month unless force)."""
    cfg = load()
    if not (cfg["enabled"] and cfg["monthly"]) and not force:
        return False
    month = month or (date.today().replace(day=1) - timedelta(days=1))
    key = f"monthly:{month.strftime('%Y-%m')}"
    _language()
    with session_scope() as db:
        done = db.scalar(select(Alert).where(Alert.key == key))
        if done and done.sent_at and not force:
            return False
        if not done:
            record(db, key, "monthly", None, f"Mesačný DMARC report {month.strftime('%Y-%m')}")
        subject, html = monthly_html(db, month)
    send(cfg, subject, html)
    with session_scope() as db:
        a = db.scalar(select(Alert).where(Alert.key == key))
        a.sent_at = utcnow()
    return True
