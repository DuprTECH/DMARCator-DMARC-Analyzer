"""Parsers for DMARC aggregate (RUA), forensic (RUF / ARF) and SMTP TLS (TLS-RPT) reports."""

import base64
import binascii
import email
import email.policy
import gzip
import hashlib
import io
import ipaddress
import json
import quopri
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import Message
from email.parser import HeaderParser
from email.utils import parseaddr, parsedate_to_datetime

from defusedxml import ElementTree as ET

MAX_DECOMPRESSED = 200 * 1024 * 1024


class ParseError(Exception):
    pass


@dataclass
class ParsedReport:
    kind: str  # aggregate | forensic | tls
    data: dict
    raw: str | None = None


@dataclass
class ExtractResult:
    reports: list[ParsedReport] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# --------------------------------------------------------------- helpers


def _ts(value) -> datetime:
    return datetime.fromtimestamp(int(float(str(value).strip())), tz=timezone.utc).replace(tzinfo=None)


def _iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _strip_ns(root):
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _text(node, path, default=None):
    if node is None:
        return default
    el = node.find(path)
    if el is None or el.text is None:
        return default
    return el.text.strip()


def decompress(blob: bytes, name: str = "") -> list[tuple[str, bytes]]:
    """Return list of (filename, bytes) – unpacks zip / gzip transparently."""
    if blob[:4] == b"PK\x03\x04":
        out = []
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            total = 0
            for info in zf.infolist():
                if info.is_dir():
                    continue
                total += info.file_size
                if total > MAX_DECOMPRESSED:
                    raise ParseError("Archív je príliš veľký")
                out.extend(decompress(zf.read(info), info.filename))
        return out
    if blob[:2] == b"\x1f\x8b":
        with gzip.GzipFile(fileobj=io.BytesIO(blob)) as gz:
            data = gz.read(MAX_DECOMPRESSED + 1)
        if len(data) > MAX_DECOMPRESSED:
            raise ParseError("Archív je príliš veľký")
        return decompress(data, re.sub(r"\.gz$", "", name, flags=re.I))
    return [(name, blob)]


# ------------------------------------------------------ aggregate (RUA)


def _period_fallback(name: str) -> tuple[int, int]:
    """Report period when date_range is missing: Unix timestamps in the file name, else the last day."""
    stamps = [int(x) for x in re.findall(r"(?<!\d)(\d{10})(?!\d)", name or "")]
    if len(stamps) >= 2:
        return min(stamps[:2]), max(stamps[:2])
    if stamps:
        return stamps[0], stamps[0] + 86399
    now = int(datetime.now(timezone.utc).timestamp())
    return now - 86400, now


def parse_aggregate(xml_bytes: bytes, name: str = "") -> ParsedReport:
    try:
        root = _strip_ns(ET.fromstring(xml_bytes))
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"Neplatné XML: {exc}") from exc
    if root.tag != "feedback":
        raise ParseError("XML nie je DMARC aggregate report")

    meta = root.find("report_metadata")
    pol = root.find("policy_published")
    # some reporters put date_range elsewhere or omit it – look anywhere, then fall back
    begin = _text(meta, "date_range/begin") or _text(root, ".//date_range/begin")
    end = _text(meta, "date_range/end") or _text(root, ".//date_range/end")
    if not begin or not end:
        begin, end = _period_fallback(name)

    def pct(v):
        try:
            return int(v) if v is not None else None
        except ValueError:
            return None

    report = {
        "org_name": _text(meta, "org_name", "unknown"),
        "org_email": _text(meta, "email"),
        "report_id": _text(meta, "report_id", ""),
        "date_begin": _ts(begin),
        "date_end": _ts(end),
        "domain": (_text(pol, "domain", "") or "").lower().rstrip("."),
        "adkim": _text(pol, "adkim", "r"),
        "aspf": _text(pol, "aspf", "r"),
        "p": _text(pol, "p"),
        "sp": _text(pol, "sp"),
        "np": _text(pol, "np"),
        "pct": pct(_text(pol, "pct")),
        "fo": _text(pol, "fo"),
        "records": [],
    }

    for rec in root.findall("record"):
        row = rec.find("row")
        pe = row.find("policy_evaluated") if row is not None else None
        ids = rec.find("identifiers")
        auth = rec.find("auth_results")
        reasons = [
            {"type": _text(r, "type"), "comment": _text(r, "comment")}
            for r in (pe.findall("reason") if pe is not None else [])
        ]
        dkim = [
            {
                "domain": (_text(d, "domain", "") or "").lower(),
                "selector": _text(d, "selector"),
                "result": (_text(d, "result", "") or "").lower(),
            }
            for d in (auth.findall("dkim") if auth is not None else [])
        ]
        spf = [
            {
                "domain": (_text(s, "domain", "") or "").lower(),
                "scope": _text(s, "scope", "mfrom"),
                "result": (_text(s, "result", "") or "").lower(),
            }
            for s in (auth.findall("spf") if auth is not None else [])
        ]
        policy_dkim = (_text(pe, "dkim", "") or "").lower()
        policy_spf = (_text(pe, "spf", "") or "").lower()
        header_from = (_text(ids, "header_from", "") or report["domain"]).lower()
        try:
            count = int(_text(row, "count", "0"))
        except ValueError:
            count = 0
        report["records"].append(
            {
                "source_ip": _text(row, "source_ip", ""),
                "count": count,
                "disposition": (_text(pe, "disposition", "none") or "none").lower(),
                "policy_dkim": policy_dkim,
                "policy_spf": policy_spf,
                "dkim_aligned": policy_dkim == "pass",
                "spf_aligned": policy_spf == "pass",
                "dmarc_pass": policy_dkim == "pass" or policy_spf == "pass",
                "reasons": reasons,
                "header_from": header_from,
                "envelope_from": (_text(ids, "envelope_from") or "").lower() or None,
                "envelope_to": (_text(ids, "envelope_to") or "").lower() or None,
                "dkim_results": dkim,
                "spf_results": spf,
            }
        )
    report["message_count"] = sum(r["count"] for r in report["records"])
    return ParsedReport("aggregate", report, raw=xml_bytes.decode("utf-8", errors="replace"))


# ------------------------------------------------------------- TLS-RPT


def parse_tls(json_bytes: bytes) -> ParsedReport:
    try:
        doc = json.loads(json_bytes)
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"Neplatný JSON: {exc}") from exc
    if "policies" not in doc or "report-id" not in doc:
        raise ParseError("JSON nie je TLS-RPT report")
    dr = doc.get("date-range", {})
    policies = []
    for p in doc.get("policies", []):
        pol = p.get("policy", {})
        summ = p.get("summary", {})
        policies.append(
            {
                "policy_domain": (pol.get("policy-domain") or "").lower().rstrip("."),
                "policy_type": pol.get("policy-type"),
                "policy_strings": pol.get("policy-string") or [],
                "mx_hosts": pol.get("mx-host") or [],
                "success_count": int(summ.get("total-successful-session-count", 0)),
                "failure_count": int(summ.get("total-failure-session-count", 0)),
                "failure_details": p.get("failure-details") or [],
            }
        )
    return ParsedReport(
        "tls",
        {
            "org_name": doc.get("organization-name", "unknown"),
            "report_id": str(doc.get("report-id")),
            "contact": doc.get("contact-info"),
            "date_begin": _iso(dr.get("start-datetime")),
            "date_end": _iso(dr.get("end-datetime")),
            "policies": policies,
        },
    )


# ----------------------------------------------------- forensic (ARF)


def _fields_from_text(text: str) -> dict:
    """ARF fields from "Name: value" lines; blank lines and other text between them are skipped,
    folded values are joined and repeated fields are comma-separated."""
    out: dict = {}
    key = None
    for line in text.splitlines():
        if key and line[:1] in (" ", "\t") and line.strip():
            out[key] += " " + line.strip()
            continue
        m = re.match(r"^([A-Za-z][\w-]*):\s*(.*)$", line)
        key = m.group(1).lower() if m else None
        if m:
            value = m.group(2).strip()
            out[key] = f"{out[key]}, {value}" if key in out else value
    return out


def _feedback_fields(part: Message) -> dict:
    payload = part.get_payload()
    if isinstance(payload, list):
        # message/* parts are parsed as an embedded message: a body that starts with a blank line or
        # is base64 / quoted-printable encoded ends up outside the parsed "headers"
        text = "".join(sub.as_string() for sub in payload if isinstance(sub, Message))
        cte = str(part.get("Content-Transfer-Encoding", "")).strip().lower()
        if cte == "base64":
            compact = re.sub(r"\s+", "", text)
            try:
                text = base64.b64decode(compact + "=" * (-len(compact) % 4)).decode("utf-8", errors="replace")
            except (binascii.Error, ValueError):
                pass
        elif cte == "quoted-printable":
            text = quopri.decodestring(text.encode()).decode("utf-8", errors="replace")
    else:
        text = (part.get_payload(decode=True) or b"").decode("utf-8", errors="replace")
    return _fields_from_text(text)


_IP_TOKEN = re.compile(r"[0-9a-fA-F:.]{3,45}")
_FORENSIC_SUBJECT = re.compile(
    r"(?:forensic|failure|auth(?:entication)?[- ]failure)\s+report\b.*?\bfor\s+(?P<domain>[\w.-]+\.[a-z]{2,63})"
    r"(?:.*?(?:\bfrom\s+(?:ip\s+)?|\bip=)(?P<ip>[0-9a-fA-F:.]{3,45}))?",
    re.I,
)


def _subject_fields(msg: Message) -> dict:
    """Fallback ARF fields from a subject like "DMARC Failure Report for <domain> (… ip=<ip>)"."""
    fields = {"feedback-type": "auth-failure"}
    m = _FORENSIC_SUBJECT.search(str(msg.get("Subject") or ""))
    if m:
        fields["reported-domain"] = m.group("domain")
        if m.group("ip"):
            fields["source-ip"] = m.group("ip")
    return fields


def _clean_ip(value: str | None) -> str | None:
    """First valid IPv4/IPv6 address in a field that may hold several addresses or extra text."""
    for token in _IP_TOKEN.findall(value or ""):
        try:
            return str(ipaddress.ip_address(token.strip(".:")))
        except ValueError:
            continue
    return None


def _text_feedback_fields(msg: Message) -> dict:
    """ARF fields written as plain text (some reporters skip the message/feedback-report part)."""
    for part in msg.walk():
        if part.get_content_type() != "text/plain":
            continue
        text = (part.get_payload(decode=True) or b"").decode(part.get_content_charset() or "utf-8", errors="replace")
        if re.search(r"^(Feedback-Type|Auth-Failure|Source-IP|Reported-Domain):", text, re.M | re.I):
            return _fields_from_text(text)
    return {}


def parse_forensic(msg: Message, fallback: dict | None = None) -> ParsedReport:
    fields: dict = {}
    headers_text = ""
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype == "message/feedback-report" and not fields:
            fields = _feedback_fields(part)
        elif ctype == "text/rfc822-headers":
            headers_text = (part.get_payload(decode=True) or b"").decode("utf-8", errors="replace")
        elif ctype == "message/rfc822" and not headers_text:
            inner = part.get_payload()
            inner = inner[0] if isinstance(inner, list) and inner else inner
            if isinstance(inner, Message):
                headers_text = "".join(f"{k}: {v}\n" for k, v in inner.items())
    if not fields:
        fields = _text_feedback_fields(msg)
    for key, value in (fallback or {}).items():
        fields.setdefault(key, value)

    orig = HeaderParser().parsestr(headers_text) if headers_text else None
    if not (fields.get("reported-domain") or fields.get("source-ip") or (orig and orig.get("From"))):
        raise ParseError("Chýba message/feedback-report časť")
    arrival = None
    for candidate in (fields.get("arrival-date"), fields.get("received-date"), orig.get("Date") if orig else None):
        if candidate:
            try:
                arrival = parsedate_to_datetime(candidate)
                if arrival.tzinfo:
                    arrival = arrival.astimezone(timezone.utc).replace(tzinfo=None)
                break
            except (TypeError, ValueError):
                continue

    header_from = None
    if orig and orig.get("From"):
        addr = parseaddr(str(orig.get("From")))[1]
        header_from = addr.split("@")[-1].lower() if "@" in addr else None

    reported = (fields.get("reported-domain") or header_from or "").lower().rstrip(".") or None
    fp_src = (headers_text or json.dumps(fields, sort_keys=True)) + (fields.get("source-ip") or "")
    data = {
        "fingerprint": hashlib.sha256(fp_src.encode()).hexdigest(),
        "arrival_date": arrival,
        "reporter": parseaddr(str(msg.get("From", "")))[1] or None,
        "feedback_type": fields.get("feedback-type"),
        "source_ip": _clean_ip(fields.get("source-ip")),
        "reported_domain": reported,
        "auth_failure": fields.get("auth-failure"),
        "delivery_result": fields.get("delivery-result"),
        "original_mail_from": fields.get("original-mail-from"),
        "original_rcpt_to": fields.get("original-rcpt-to"),
        "dkim_domain": fields.get("dkim-domain"),
        "subject": str(orig.get("Subject")) if orig and orig.get("Subject") else None,
        "header_from": header_from,
        "feedback_fields": fields,
        "original_headers": headers_text[:64000] or None,
    }
    return ParsedReport("forensic", data)


# ------------------------------------------------------------- routing


def _classify_blob(name: str, blob: bytes, result: ExtractResult):
    for fname, data in decompress(blob, name):
        head = data[:4096].lstrip(b"\xef\xbb\xbf \r\n\t")
        try:
            if head.startswith(b"<") and b"<feedback" in data[:65536]:
                result.reports.append(parse_aggregate(data, fname))
            elif head.startswith(b"{"):
                result.reports.append(parse_tls(data))
            elif fname.lower().endswith(".eml") or b"\nContent-Type:" in data[:65536] or data[:65536].startswith(b"Received:"):
                sub = extract_from_email(data)
                result.reports.extend(sub.reports)
                result.errors.extend(sub.errors)
        except ParseError as exc:
            result.errors.append(f"{fname or 'príloha'}: {exc}")


REPORT_TYPES = (
    "application/zip",
    "application/x-zip-compressed",
    "application/gzip",
    "application/x-gzip",
    "application/xml",
    "text/xml",
    "application/json",
    "application/tlsrpt+gzip",
    "application/tlsrpt+json",
    "application/octet-stream",
)


def extract_from_email(raw: bytes) -> ExtractResult:
    """Extract every report contained in a raw RFC 5322 message."""
    result = ExtractResult()
    msg = email.message_from_bytes(raw, policy=email.policy.compat32)

    part_types = {p.get_content_type() for p in msg.walk()}
    is_arf = (msg.get_content_type() == "multipart/report"
              and str(msg.get_param("report-type") or "").lower() == "feedback-report")
    if is_arf or "message/feedback-report" in part_types:
        try:
            result.reports.append(parse_forensic(msg, _subject_fields(msg)))
        except ParseError as exc:
            result.errors.append(str(exc))
        return result

    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        fname = part.get_filename() or ""
        if ctype in REPORT_TYPES or re.search(r"\.(zip|gz|xml|json)$", fname, re.I):
            payload = part.get_payload(decode=True)
            if payload:
                _classify_blob(fname, payload, result)

    if not result.reports and not result.errors:
        # forensic report without the standard ARF structure: plain-text fields, or at least
        # "… Forensic Report for <domain> from IP <ip>" in the subject
        fallback = _subject_fields(msg)
        if "reported-domain" in fallback or _text_feedback_fields(msg):
            try:
                result.reports.append(parse_forensic(msg, fallback))
            except ParseError as exc:
                result.errors.append(str(exc))
    return result


def extract_from_file(name: str, blob: bytes) -> ExtractResult:
    result = ExtractResult()
    _classify_blob(name, blob, result)
    if not result.reports and not result.errors:
        result.errors.append(f"{name}: nerozpoznaný formát")
    return result
