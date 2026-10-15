"""Maintenance commands for cron and administrators.

    python -m app.manage poll [--source ID]         fetch reports from mailboxes now
    python -m app.manage dns [--missing]            re-run DNS checks
    python -m app.manage prune --days N [--dry-run] delete reports older than N days
    python -m app.manage status [--max-age MIN]     mailbox health; exit code 1 on problems
    python -m app.manage set-password USERNAME      set a local account's password
    python -m app.manage create-admin USERNAME      create a local admin account
    python -m app.manage auth-reset                 drop sign-in settings saved in the web UI (back to .env)
    python -m app.manage version                    print the version
"""

import argparse
import getpass
import os
import sys
from datetime import timedelta

from sqlalchemy import delete, func, select

from . import __version__
from .db import init_db, session_scope
from .ingest.service import refresh_dns, run_all_sources, run_source
from .models import (
    AggregateRecord,
    AggregateReport,
    ForensicReport,
    IngestLog,
    MailSource,
    TlsReport,
    User,
    utcnow,
)
from .security import hash_password

BATCH = 1000


def poll(source: int | None = None) -> int:
    if source:
        print(run_source(source))
    else:
        run_all_sources()
        print("All enabled mailboxes processed.")
    return 0


def dns(missing: bool = False) -> int:
    refresh_dns(only_missing=missing)
    print("DNS check finished.")
    return 0


def prune(days: int, dry_run: bool = False) -> dict:
    """Delete aggregate, forensic and TLS reports (and old ingest log lines) older than `days`."""
    cutoff = utcnow() - timedelta(days=days)
    counts = {"aggregate": 0, "forensic": 0, "tls": 0, "log": 0}
    with session_scope() as db:
        if dry_run:
            counts["aggregate"] = db.scalar(select(func.count(AggregateReport.id)).where(AggregateReport.date_end < cutoff))
            counts["forensic"] = db.scalar(select(func.count(ForensicReport.id)).where(
                func.coalesce(ForensicReport.arrival_date, ForensicReport.received_at) < cutoff))
            counts["tls"] = db.scalar(select(func.count(TlsReport.id)).where(TlsReport.date_end < cutoff))
            counts["log"] = db.scalar(select(func.count(IngestLog.id)).where(IngestLog.created_at < cutoff))
            return counts
    # aggregate reports in batches – each report can have many records
    while True:
        with session_scope() as db:
            ids = list(db.scalars(select(AggregateReport.id).where(AggregateReport.date_end < cutoff).limit(BATCH)))
            if not ids:
                break
            db.execute(delete(AggregateRecord).where(AggregateRecord.report_id.in_(ids)))
            db.execute(delete(AggregateReport).where(AggregateReport.id.in_(ids)))
            counts["aggregate"] += len(ids)
    with session_scope() as db:
        counts["forensic"] = db.execute(delete(ForensicReport).where(
            func.coalesce(ForensicReport.arrival_date, ForensicReport.received_at) < cutoff)).rowcount
        counts["tls"] = db.execute(delete(TlsReport).where(TlsReport.date_end < cutoff)).rowcount
        counts["log"] = db.execute(delete(IngestLog).where(IngestLog.created_at < cutoff)).rowcount
    return counts


def status(max_age: int = 60) -> int:
    """Print mailbox status. Returns 1 when a source failed or hasn't run within `max_age` minutes."""
    problems = 0
    with session_scope() as db:
        sources = db.scalars(select(MailSource).order_by(MailSource.name)).all()
        if not sources:
            print("No mailboxes configured yet.")
            return 0
        for s in sources:
            stale = s.enabled and (not s.last_run or s.last_run < utcnow() - timedelta(minutes=max_age))
            bad = s.enabled and (s.last_status == "error" or stale)
            problems += bad
            state = "disabled" if not s.enabled else ("STALE" if stale else (s.last_status or "never run").upper())
            print(f"[{state:9}] {s.name} – last run {s.last_run or '-'} UTC – {s.last_message or ''}")
        newest = db.scalar(select(func.max(AggregateReport.received_at)))
        print(f"Newest aggregate report stored: {newest or '-'} UTC")
    return 1 if problems else 0


def set_password(username: str, password: str | None = None, create_admin: bool = False) -> int:
    password = password or os.environ.get("DMARCATOR_PASSWORD") or os.environ.get("DMARCULA_PASSWORD") or getpass.getpass("New password: ")
    if len(password) < 10:
        print("Password must have at least 10 characters.", file=sys.stderr)
        return 2
    with session_scope() as db:
        user = db.scalar(select(User).where(func.lower(User.username) == username.lower()))
        if create_admin:
            if user:
                print(f"User {username} already exists.", file=sys.stderr)
                return 2
            db.add(User(username=username.lower(), password_hash=hash_password(password), role="admin",
                        auth_source="local", display_name=username))
            print(f"Admin {username} created.")
            return 0
        if not user or user.auth_source != "local":
            print(f"Local user {username} not found.", file=sys.stderr)
            return 2
        user.password_hash = hash_password(password)
        user.is_active = True
    print(f"Password for {username} updated.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.manage", description="DMARCator maintenance")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("poll", help="fetch reports from mailboxes now")
    p.add_argument("--source", type=int, help="only this mailbox id")
    p = sub.add_parser("dns", help="re-run DNS checks")
    p.add_argument("--missing", action="store_true", help="only domains never checked")
    p = sub.add_parser("prune", help="delete reports older than N days")
    p.add_argument("--days", type=int, required=True)
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("status", help="mailbox health check (exit 1 on problems)")
    p.add_argument("--max-age", type=int, default=60, help="minutes since the last successful run")
    p = sub.add_parser("set-password", help="set a local account's password")
    p.add_argument("username")
    p = sub.add_parser("create-admin", help="create a local admin account")
    p.add_argument("username")
    sub.add_parser("auth-reset", help="drop sign-in settings saved in the web UI (back to .env)")
    sub.add_parser("version", help="print the version")
    args = ap.parse_args(argv)

    if args.cmd == "version":
        print(f"DMARCator {__version__}")
        return 0
    init_db()
    if args.cmd == "auth-reset":
        from .settings_store import clear_auth

        clear_auth()
        print("Sign-in settings from the web UI removed – .env values apply again (restart the service).")
        return 0
    if args.cmd == "poll":
        return poll(args.source)
    if args.cmd == "dns":
        return dns(args.missing)
    if args.cmd == "prune":
        if args.days < 30:
            print("Refusing to prune with --days < 30.", file=sys.stderr)
            return 2
        counts = prune(args.days, args.dry_run)
        verb = "Would delete" if args.dry_run else "Deleted"
        print(f"{verb}: {counts['aggregate']} aggregate, {counts['forensic']} forensic, {counts['tls']} TLS reports, "
              f"{counts['log']} log lines older than {args.days} days.")
        return 0
    if args.cmd == "status":
        return status(args.max_age)
    if args.cmd == "set-password":
        return set_password(args.username)
    if args.cmd == "create-admin":
        return set_password(args.username, create_admin=True)
    return 2


if __name__ == "__main__":
    sys.exit(main())
