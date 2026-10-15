import csv
import io
import re
import threading

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import settings_store
from ..analysis.cache import invalidate
from ..auth import ldap_auth, oidc
from ..config import get_settings
from ..db import get_db
from ..ingest.parser import extract_from_file
from ..ingest.service import (
    add_log,
    open_mailbox,
    refresh_dns,
    run_source,
    store_result,
)
from ..models import (
    AggregateReport,
    Domain,
    ForensicReport,
    IngestLog,
    ROLES,
    MailSource,
    TlsReport,
    User,
)
from ..security import (
    admin_required,
    decrypt_json,
    encrypt_json,
    hash_password,
    verify_csrf,
)
from ..web import flash, render

router = APIRouter(prefix="/admin", dependencies=[Depends(admin_required)])

SECRET_FIELDS = ("client_secret", "password", "cert_pem")
BOOL_FIELDS = ("only_unread", "mark_read", "tls_verify")
FIELDS = {
    "graph": ("tenant_id", "client_id", "client_secret", "cert_pem", "cert_thumbprint", "mailbox", "folder",
              "processed_folder", "failed_folder", "noreport_folder", "authority_host", "graph_endpoint", "cleanup_days"),
    "imap": ("host", "port", "security", "auth", "username", "password", "tenant_id", "client_id", "client_secret",
             "folder", "processed_folder", "failed_folder", "noreport_folder"),
}


# ------------------------------------------------------------- mailboxes


@router.get("/display")
def display_settings(request: Request):
    return render(request, "admin/display.html")


@router.post("/display", dependencies=[Depends(verify_csrf)])
async def display_settings_save(request: Request, me: User = Depends(admin_required)):
    form = await request.form()
    try:
        settings_store.save_display(dict(form), me.username)
    except ValueError as exc:
        flash(request, str(exc), "error")
        return RedirectResponse("/admin/display", 303)
    flash(request, "Nastavenia zobrazenia uložené.")
    return RedirectResponse("/admin/display", 303)


@router.get("/sources")
def sources(request: Request, db: Session = Depends(get_db)):
    items = db.scalars(select(MailSource).order_by(MailSource.name)).all()
    logs = db.scalars(select(IngestLog).order_by(IngestLog.id.desc()).limit(60)).all()
    return render(request, "admin/sources.html", {"items": items, "logs": logs,
                                                  "poll": get_settings().poll_interval_minutes})


@router.get("/sources/new")
def source_new(request: Request, kind: str = "graph"):
    return render(request, "admin/source_form.html", {"src": None, "kind": kind, "cfg": {
        "folder": "Inbox" if kind == "graph" else "INBOX", "processed_folder": "Inbox/DMARC-processed" if kind == "graph" else "DMARC-processed",
        "only_unread": True, "mark_read": True, "tls_verify": True, "port": 993, "security": "ssl", "auth": "password"}})


@router.get("/sources/{sid}")
def source_edit(sid: int, request: Request, db: Session = Depends(get_db)):
    src = db.get(MailSource, sid)
    if not src:
        raise HTTPException(404)
    cfg = decrypt_json(src.config_encrypted)
    for k in SECRET_FIELDS:
        if cfg.get(k):
            cfg[k] = ""
            cfg[f"has_{k}"] = True
    return render(request, "admin/source_form.html", {"src": src, "kind": src.kind, "cfg": cfg})


@router.post("/sources/save", dependencies=[Depends(verify_csrf)])
async def source_save(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    kind = form.get("kind")
    if kind not in FIELDS:
        raise HTTPException(400)
    sid = int(form.get("id") or 0)
    src = db.get(MailSource, sid) if sid else None
    old = decrypt_json(src.config_encrypted) if src else {}
    cfg = {}
    for k in FIELDS[kind]:
        v = str(form.get(k) or "").strip() if k != "cert_pem" else str(form.get(k) or "")
        cfg[k] = v if (v or k not in SECRET_FIELDS) else old.get(k, "")
    for k in BOOL_FIELDS:
        cfg[k] = form.get(k) == "on"
    cfg["cleanup_enabled"] = kind == "graph" and form.get("cleanup_enabled") == "on"
    cfg["sync_domains"] = kind == "graph" and form.get("sync_domains") == "on"
    if cfg["cleanup_enabled"]:
        from ..ingest.retention import retention_days

        try:
            cfg["cleanup_days"] = retention_days(cfg)
        except ValueError as exc:
            flash(request, str(exc), "error")
            return RedirectResponse(f"/admin/sources/{sid}" if sid else f"/admin/sources/new?kind={kind}", 303)
    required = ("tenant_id", "client_id", "mailbox") if kind == "graph" else ("host", "username")
    missing = [k for k in required if not cfg.get(k)]
    if missing:
        flash(request, "Chýbajú povinné polia: " + ", ".join(missing), "error")
        return RedirectResponse(f"/admin/sources/{sid}" if sid else f"/admin/sources/new?kind={kind}", 303)
    if not src:
        src = MailSource(kind=kind, name="", config_encrypted="")
        db.add(src)
    src.name = str(form.get("name") or cfg.get("mailbox") or cfg.get("username"))
    src.enabled = form.get("enabled") == "on"
    src.config_encrypted = encrypt_json(cfg)
    db.commit()
    if cfg["sync_domains"] and src.enabled:
        threading.Thread(target=refresh_dns, daemon=True).start()  # reads the tenant domains, then checks DNS
    flash(request, f"Zdroj {src.name} uložený.")
    return RedirectResponse("/admin/sources", 303)


@router.post("/sources/{sid}/test", dependencies=[Depends(verify_csrf)])
def source_test(sid: int, request: Request, db: Session = Depends(get_db)):
    src = db.get(MailSource, sid)
    if not src:
        raise HTTPException(404)
    try:
        mbox = open_mailbox(src)
        msg = mbox.test()
        mbox.close()
        flash(request, f"{src.name}: {msg}")
    except Exception as exc:  # noqa: BLE001
        flash(request, f"{src.name}: {exc}", "error")
    return RedirectResponse("/admin/sources", 303)


@router.post("/sources/{sid}/run", dependencies=[Depends(verify_csrf)])
def source_run(sid: int, request: Request):
    threading.Thread(target=run_source, args=(sid,), daemon=True).start()
    flash(request, "Spracovanie spustené na pozadí – výsledok sa zobrazí v logu.")
    return RedirectResponse("/admin/sources", 303)


@router.post("/sources/{sid}/delete", dependencies=[Depends(verify_csrf)])
def source_delete(sid: int, request: Request, db: Session = Depends(get_db)):
    src = db.get(MailSource, sid)
    if src:
        db.delete(src)
        db.commit()
        flash(request, f"Zdroj {src.name} odstránený.")
    return RedirectResponse("/admin/sources", 303)


# ---------------------------------------------------------------- upload


@router.get("/upload")
def upload_page(request: Request):
    return render(request, "admin/upload.html")


@router.post("/upload", dependencies=[Depends(verify_csrf)])
async def upload(request: Request, files: list[UploadFile] = File(...), db: Session = Depends(get_db)):
    limit = get_settings().max_attachment_mb * 1024 * 1024
    totals = {"new": 0, "duplicate": 0, "errors": []}
    for up in files:
        blob = await up.read(limit + 1)
        if len(blob) > limit:
            totals["errors"].append(f"{up.filename}: súbor je príliš veľký")
            continue
        st = store_result(db, extract_from_file(up.filename or "", blob), "upload")
        totals["new"] += st["new"]
        totals["duplicate"] += st["duplicate"]
        totals["errors"].extend(st["errors"])
    add_log(db, "upload", f"Import: {totals['new']} nových, {totals['duplicate']} duplicít, {len(totals['errors'])} chýb")
    db.commit()
    if totals["new"]:
        threading.Thread(target=refresh_dns, kwargs={"only_missing": True}, daemon=True).start()
    return render(request, "admin/upload.html", {"result": totals})


# ----------------------------------------------------------------- users


@router.get("/users")
def users(request: Request, db: Session = Depends(get_db)):
    return render(request, "admin/users.html", {"items": db.scalars(select(User).order_by(User.username)).all()})


@router.post("/users/create", dependencies=[Depends(verify_csrf)])
def user_create(request: Request, username: str = Form(...), password: str = Form(...), role: str = Form("viewer"),
                display_name: str = Form(""), email: str = Form(""), db: Session = Depends(get_db)):
    username = username.strip().lower()
    if db.scalar(select(User).where(User.username == username)):
        flash(request, "Používateľ už existuje.", "error")
    elif len(password) < 10:
        flash(request, "Heslo musí mať aspoň 10 znakov.", "error")
    else:
        db.add(User(username=username, password_hash=hash_password(password), role=role if role in ROLES else "viewer",
                    display_name=display_name or None, email=email or None, auth_source="local"))
        db.commit()
        flash(request, f"Používateľ {username} vytvorený.")
    return RedirectResponse("/admin/users", 303)


@router.post("/users/{uid}/update", dependencies=[Depends(verify_csrf)])
def user_update(uid: int, request: Request, role: str = Form(...), is_active: str = Form("off"),
                password: str = Form(""), me: User = Depends(admin_required), db: Session = Depends(get_db)):
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404)
    if u.id == me.id and (role != "admin" or is_active != "on"):
        flash(request, "Nemôžete si odobrať admin práva ani deaktivovať vlastný účet.", "error")
        return RedirectResponse("/admin/users", 303)
    u.role = role if role in ROLES else "viewer"
    u.is_active = is_active == "on"
    if password:
        if u.auth_source != "local" or len(password) < 10:
            flash(request, "Heslo sa dá nastaviť iba lokálnemu účtu a musí mať aspoň 10 znakov.", "error")
            return RedirectResponse("/admin/users", 303)
        u.password_hash = hash_password(password)
    db.commit()
    flash(request, f"Používateľ {u.username} upravený.")
    return RedirectResponse("/admin/users", 303)


@router.post("/users/{uid}/delete", dependencies=[Depends(verify_csrf)])
def user_delete(uid: int, request: Request, me: User = Depends(admin_required), db: Session = Depends(get_db)):
    u = db.get(User, uid)
    if u and u.id != me.id:
        db.delete(u)
        db.commit()
        flash(request, f"Používateľ {u.username} odstránený.")
    return RedirectResponse("/admin/users", 303)


# --------------------------------------------------------------- domains


DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}$")


def _add_domains(request: Request, db: Session, names: set[str]) -> None:
    """Add new domains, check their DNS in the background and flash a summary."""
    invalid = sorted(n for n in names if not DOMAIN_RE.match(n))
    valid = names - set(invalid)
    existing = set(db.scalars(select(Domain.name).where(Domain.name.in_(valid)))) if valid else set()
    for name in sorted(valid - existing):
        db.add(Domain(name=name))
    db.commit()
    added = len(valid - existing)
    if added:
        invalidate()
        threading.Thread(target=refresh_dns, kwargs={"only_missing": True}, daemon=True).start()
    msg = f"Pridané domény: {added}, už existovalo: {len(existing)}."
    if invalid:
        msg += f" Neplatné: {', '.join(invalid[:10])}."
    flash(request, msg, "warn" if invalid else "ok")


def domains_from_csv(blob: bytes) -> set[str]:
    """Domains from a CSV / TXT file: in every row the first cell that looks like a domain, so header
    rows and extra columns (status, DNS records, … from admin-center exports) are skipped."""
    try:
        text = blob.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = blob.decode("cp1250", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    names: set[str] = set()
    for row in csv.reader(io.StringIO(text), dialect):
        for cell in row:
            name = cell.strip().strip('"').lower().rstrip(".")
            if DOMAIN_RE.match(name):
                if not name.endswith(".onmicrosoft.com"):
                    names.add(name)
                break
    return names


@router.post("/domains/add", dependencies=[Depends(verify_csrf)])
async def domains_add(request: Request, domains: str = Form(""), file: UploadFile | None = File(None),
                      db: Session = Depends(get_db)):
    names = {n.strip().lower().rstrip(".") for n in re.split(r"[\s,;]+", domains) if n.strip()}
    if file is not None and file.filename:
        blob = await file.read(5 * 1024 * 1024 + 1)
        if len(blob) > 5 * 1024 * 1024:
            flash(request, f"{file.filename}: súbor je príliš veľký", "error")
            return RedirectResponse("/domains", 303)
        names |= domains_from_csv(blob)
    if not names:
        flash(request, "V zozname ani v súbore sa nenašla žiadna doména.", "warn")
        return RedirectResponse("/domains", 303)
    _add_domains(request, db, names)
    return RedirectResponse("/domains", 303)


@router.post("/domains/import-m365", dependencies=[Depends(verify_csrf)])
def domains_import_m365(request: Request, db: Session = Depends(get_db)):
    sources = db.scalars(select(MailSource).where(MailSource.kind == "graph")).all()
    if not sources:
        flash(request, "Najprv pridajte schránku typu Exchange Online (Graph) – jej aplikácia sa použije na čítanie domén.", "warn")
        return RedirectResponse("/domains", 303)
    names: set[str] = set()
    for src in sources:
        try:
            names |= set(open_mailbox(src).tenant_domains())
        except Exception as exc:  # noqa: BLE001
            flash(request, f"{src.name}: {exc} – aplikácia potrebuje oprávnenie Domain.Read.All (Application) so súhlasom správcu.", "error")
            return RedirectResponse("/domains", 303)
    if not names:
        flash(request, "Tenant nemá žiadne overené vlastné domény.", "warn")
        return RedirectResponse("/domains", 303)
    _add_domains(request, db, names)
    return RedirectResponse("/domains", 303)


@router.post("/dns-refresh", dependencies=[Depends(verify_csrf)])
def dns_refresh(request: Request):
    threading.Thread(target=refresh_dns, daemon=True).start()
    flash(request, "DNS kontrola všetkých domén spustená na pozadí.")
    return RedirectResponse("/domains", 303)


@router.post("/domains/{did}/ignore", dependencies=[Depends(verify_csrf)])
def domain_ignore(did: int, request: Request, ignored: str = Form("0"), db: Session = Depends(get_db)):
    dom = db.get(Domain, did)
    if not dom:
        raise HTTPException(404)
    dom.ignored = ignored == "1"
    db.commit()
    invalidate()
    flash(request, f"Doména {dom.name} sa už nekontroluje." if dom.ignored else f"Doména {dom.name} sa znova kontroluje.")
    return RedirectResponse(f"/domains/{dom.name}", 303)


@router.post("/domains/{did}/delete", dependencies=[Depends(verify_csrf)])
def domain_delete(did: int, request: Request, db: Session = Depends(get_db)):
    dom = db.get(Domain, did)
    if dom:
        db.execute(delete(AggregateReport).where(AggregateReport.domain == dom.name))
        db.execute(delete(ForensicReport).where(ForensicReport.reported_domain == dom.name))
        db.execute(delete(TlsReport).where(TlsReport.policy_domain == dom.name))
        db.delete(dom)
        db.commit()
        invalidate()
        flash(request, f"Doména {dom.name} a všetky jej reporty boli odstránené.")
    return RedirectResponse("/domains", 303)


# ------------------------------------------------------------ sign-in settings


def _auth_page(request: Request, cfg, ldap_trace=None, oidc_trace=None, test_user: str = ""):
    stored = settings_store.overrides()
    return render(request, "admin/auth.html", {
        "cfg": cfg, "stored": bool(stored), "ldap_trace": ldap_trace, "oidc_trace": oidc_trace, "test_user": test_user,
        "redirect_uri": f"{cfg.base_url.rstrip('/')}/auth/oidc/callback",
    })


@router.get("/auth")
def auth_settings(request: Request):
    return _auth_page(request, settings_store.effective_settings())


@router.post("/auth", dependencies=[Depends(verify_csrf)])
async def auth_settings_save(request: Request, me: User = Depends(admin_required)):
    form = await request.form()
    current = settings_store.effective_settings()
    values = settings_store.parse_form(form, current)
    candidate = current.model_copy(update=values)
    action = form.get("action", "save")

    if action == "test_ldap":
        trace: list = []
        ldap_auth.authenticate(str(form.get("test_username") or ""), str(form.get("test_password") or ""),
                               candidate.model_copy(update={"ldap_enabled": True}), trace)
        return _auth_page(request, candidate, ldap_trace=trace, test_user=str(form.get("test_username") or ""))
    if action == "test_oidc":
        return _auth_page(request, candidate, oidc_trace=oidc.test_discovery(candidate))

    if not values["local_login_enabled"] and me.auth_source == "local":
        flash(request, "Lokálne prihlásenie môžete vypnúť až po prihlásení cez LDAP alebo SSO "
                       "(overí sa tým, že funguje).", "error")
        return _auth_page(request, candidate)
    if not (values["local_login_enabled"] or values["ldap_enabled"] or values["oidc_enabled"]):
        flash(request, "Aspoň jeden spôsob prihlásenia musí zostať zapnutý.", "error")
        return _auth_page(request, candidate)
    settings_store.save_auth(values, me.username)
    flash(request, "Nastavenia prihlásenia uložené.")
    return RedirectResponse("/admin/auth", 303)


@router.post("/auth/reset", dependencies=[Depends(verify_csrf)])
def auth_settings_reset(request: Request):
    settings_store.clear_auth()
    flash(request, "Nastavenia prihlásenia z webu boli zrušené – platia hodnoty z .env.")
    return RedirectResponse("/admin/auth", 303)
