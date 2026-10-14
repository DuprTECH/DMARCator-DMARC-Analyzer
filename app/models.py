from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

# MariaDB/MySQL TEXT is capped at 64 KB – raw report XML and mail headers can be larger
LongText = Text().with_variant(LONGTEXT(), "mysql", "mariadb")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255))
    auth_source: Mapped[str] = mapped_column(String(16), default="local")  # local | ldap | oidc
    password_hash: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="viewer")  # admin | viewer
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login: Mapped[datetime | None] = mapped_column(DateTime)


class MailSource(Base):
    __tablename__ = "mail_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(16))  # graph | imap
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config_encrypted: Mapped[str] = mapped_column(Text)
    last_run: Mapped[datetime | None] = mapped_column(DateTime)
    last_status: Mapped[str | None] = mapped_column(String(32))
    last_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class IngestLog(Base):
    __tablename__ = "ingest_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    source: Mapped[str] = mapped_column(String(255))
    level: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(Text)


class Domain(Base):
    __tablename__ = "domains"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime)
    dns: Mapped[dict | None] = mapped_column(JSON)
    dns_checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    dns_score: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text)


class AggregateReport(Base):
    __tablename__ = "aggregate_reports"
    __table_args__ = (
        UniqueConstraint("org_name", "report_id", "domain", name="uq_aggregate"),
        Index("ix_rep_domain_end", "domain", "date_end"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    org_name: Mapped[str] = mapped_column(String(255), index=True)
    org_email: Mapped[str | None] = mapped_column(String(255))
    report_id: Mapped[str] = mapped_column(String(255))
    domain: Mapped[str] = mapped_column(String(255), index=True)
    date_begin: Mapped[datetime] = mapped_column(DateTime, index=True)
    date_end: Mapped[datetime] = mapped_column(DateTime)
    adkim: Mapped[str | None] = mapped_column(String(4))
    aspf: Mapped[str | None] = mapped_column(String(4))
    p: Mapped[str | None] = mapped_column(String(16))
    sp: Mapped[str | None] = mapped_column(String(16))
    np: Mapped[str | None] = mapped_column(String(16))
    pct: Mapped[int | None] = mapped_column(Integer)
    fo: Mapped[str | None] = mapped_column(String(16))
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str | None] = mapped_column(String(255))
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    raw_xml: Mapped[str | None] = mapped_column(LongText)

    records: Mapped[list["AggregateRecord"]] = relationship(
        back_populates="report", cascade="all, delete-orphan", passive_deletes=True
    )


class AggregateRecord(Base):
    __tablename__ = "aggregate_records"
    __table_args__ = (
        Index("ix_rec_domain_day", "domain", "day"),
        Index("ix_rec_day", "day"),
        Index("ix_rec_ip", "source_ip"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("aggregate_reports.id", ondelete="CASCADE"), index=True)
    domain: Mapped[str] = mapped_column(String(255))  # policy_published domain (denormalized)
    day: Mapped[date] = mapped_column(Date)  # report begin date (denormalized)
    org_name: Mapped[str] = mapped_column(String(255))  # reporter (denormalized)
    source_ip: Mapped[str] = mapped_column(String(64))
    count: Mapped[int] = mapped_column(Integer)
    disposition: Mapped[str | None] = mapped_column(String(16))
    policy_dkim: Mapped[str | None] = mapped_column(String(16))
    policy_spf: Mapped[str | None] = mapped_column(String(16))
    dmarc_pass: Mapped[bool] = mapped_column(Boolean)
    spf_aligned: Mapped[bool] = mapped_column(Boolean)
    dkim_aligned: Mapped[bool] = mapped_column(Boolean)
    reasons: Mapped[list | None] = mapped_column(JSON)
    header_from: Mapped[str | None] = mapped_column(String(255))
    envelope_from: Mapped[str | None] = mapped_column(String(255))
    envelope_to: Mapped[str | None] = mapped_column(String(255))
    dkim_results: Mapped[list | None] = mapped_column(JSON)
    spf_results: Mapped[list | None] = mapped_column(JSON)
    # first DKIM / SPF result, denormalized so failures can be grouped in SQL
    dkim_domain: Mapped[str | None] = mapped_column(String(255))
    dkim_result: Mapped[str | None] = mapped_column(String(16))
    spf_domain: Mapped[str | None] = mapped_column(String(255))
    spf_result: Mapped[str | None] = mapped_column(String(16))
    ptr: Mapped[str | None] = mapped_column(String(255))
    base_domain: Mapped[str | None] = mapped_column(String(255))
    sender: Mapped[str | None] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(16), index=True)  # compliant|forwarded|noncompliant|threat
    country: Mapped[str | None] = mapped_column(String(64))
    asn_org: Mapped[str | None] = mapped_column(String(255))

    report: Mapped[AggregateReport] = relationship(back_populates="records")


class ForensicReport(Base):
    __tablename__ = "forensic_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    arrival_date: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    reporter: Mapped[str | None] = mapped_column(String(255))
    feedback_type: Mapped[str | None] = mapped_column(String(64))
    source_ip: Mapped[str | None] = mapped_column(String(64))
    reported_domain: Mapped[str | None] = mapped_column(String(255), index=True)
    auth_failure: Mapped[str | None] = mapped_column(String(64))
    delivery_result: Mapped[str | None] = mapped_column(String(64))
    original_mail_from: Mapped[str | None] = mapped_column(String(255))
    original_rcpt_to: Mapped[str | None] = mapped_column(String(255))
    dkim_domain: Mapped[str | None] = mapped_column(String(255))
    subject: Mapped[str | None] = mapped_column(Text)
    header_from: Mapped[str | None] = mapped_column(String(255))
    ptr: Mapped[str | None] = mapped_column(String(255))
    country: Mapped[str | None] = mapped_column(String(64))
    feedback_fields: Mapped[dict | None] = mapped_column(JSON)
    original_headers: Mapped[str | None] = mapped_column(LongText)
    source: Mapped[str | None] = mapped_column(String(255))


class TlsReport(Base):
    __tablename__ = "tls_reports"
    __table_args__ = (UniqueConstraint("org_name", "report_id", "policy_domain", name="uq_tls"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    org_name: Mapped[str] = mapped_column(String(255))
    report_id: Mapped[str] = mapped_column(String(255))
    contact: Mapped[str | None] = mapped_column(String(255))
    date_begin: Mapped[datetime] = mapped_column(DateTime, index=True)
    date_end: Mapped[datetime] = mapped_column(DateTime)
    policy_domain: Mapped[str] = mapped_column(String(255), index=True)
    policy_type: Mapped[str | None] = mapped_column(String(32))
    policy_strings: Mapped[list | None] = mapped_column(JSON)
    mx_hosts: Mapped[list | None] = mapped_column(JSON)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_details: Mapped[list | None] = mapped_column(JSON)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    source: Mapped[str | None] = mapped_column(String(255))


class IpInfo(Base):
    __tablename__ = "ip_info"

    ip: Mapped[str] = mapped_column(String(64), primary_key=True)
    ptr: Mapped[str | None] = mapped_column(String(255))
    base_domain: Mapped[str | None] = mapped_column(String(255))
    country: Mapped[str | None] = mapped_column(String(64))
    country_code: Mapped[str | None] = mapped_column(String(4))
    asn: Mapped[int | None] = mapped_column(Integer)
    asn_org: Mapped[str | None] = mapped_column(String(255))
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AppSetting(Base):
    """Settings changed in the web UI (stored encrypted, override values from .env)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    updated_by: Mapped[str | None] = mapped_column(String(255))
