"""Live DNS posture checks for a domain: DMARC, SPF, DKIM, MX, MTA-STS, TLS-RPT, BIMI."""

import base64
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import dns.exception
import dns.resolver
import httpx
from cryptography.hazmat.primitives.serialization import load_der_public_key

from ..config import get_settings
from .senders import base_domain, identify

COMMON_DKIM_SELECTORS = [
    "selector1", "selector2", "google", "default", "dkim", "mail", "k1", "k2", "s1", "s2",
    "smtp", "mxvault", "everlytickey1", "mandrill", "mte1", "pm", "zoho", "sig1", "fm1", "protonmail",
]


class DnsLookupError(Exception):
    """The DNS server did not answer (timeout / SERVFAIL) – absence of a record cannot be concluded."""


def configured_servers() -> list[str]:
    return [x.strip() for x in get_settings().dns_servers.split(",") if x.strip()]


def _resolver() -> dns.resolver.Resolver:
    servers = configured_servers()
    r = dns.resolver.Resolver(configure=not servers)
    if servers:
        r.nameservers = servers
    r.lifetime = 6.0
    r.timeout = 2.0
    return r


def txt(name: str) -> list[str] | None:
    """Return TXT strings, [] when the name exists without TXT, None on NXDOMAIN.

    Raises DnsLookupError when the resolver fails, so a broken resolver is never
    reported as a missing record.
    """
    try:
        ans = _resolver().resolve(name, "TXT")
        return [b"".join(r.strings).decode("utf-8", errors="replace") for r in ans]
    except dns.resolver.NXDOMAIN:
        return None
    except dns.resolver.NoAnswer:
        return []
    except (dns.resolver.NoNameservers, dns.exception.Timeout) as exc:
        raise DnsLookupError(f"{name}: {exc.__class__.__name__}") from exc


def _issue(level: str, msg: str) -> dict:
    return {"level": level, "msg": msg}


def _status(issues: list[dict], present: bool = True) -> str:
    if not present or any(i["level"] == "error" for i in issues):
        return "error"
    if any(i["level"] == "warn" for i in issues):
        return "warn"
    return "ok"


def parse_tags(record: str) -> dict:
    tags = {}
    for part in record.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            tags[k.strip().lower()] = v.strip()
    return tags


# ------------------------------------------------------------------ DMARC


def check_dmarc(domain: str) -> dict:
    issues: list[dict] = []
    records = [r for r in (txt(f"_dmarc.{domain}") or []) if r.lower().startswith("v=dmarc1")]
    inherited_from = None
    if not records:
        org = base_domain(domain)
        if org and org != domain:
            records = [r for r in (txt(f"_dmarc.{org}") or []) if r.lower().startswith("v=dmarc1")]
            if records:
                inherited_from = org
    if not records:
        return {"record": None, "tags": {}, "status": "error",
                "issues": [_issue("error", "Doména nemá DMARC záznam – ktokoľvek ju môže zneužiť na spoofing.")]}
    if len(records) > 1:
        issues.append(_issue("error", "Viac DMARC záznamov – príjemcovia DMARC ignorujú."))
    record = records[0]
    tags = parse_tags(record)
    p = tags.get("p", "").lower()
    policy = tags.get("sp", p).lower() if inherited_from else p
    if p not in ("none", "quarantine", "reject"):
        issues.append(_issue("error", "Chýba alebo je neplatný tag p=."))
    elif policy == "none":
        issues.append(_issue("warn", "Politika p=none iba monitoruje – podvrhnuté e-maily sa doručia."))
    elif policy == "quarantine":
        issues.append(_issue("info", "Politika quarantine – zvážte prechod na p=reject."))
    if inherited_from:
        issues.append(_issue("info", f"Záznam zdedený z organizačnej domény {inherited_from} (platí sp=)."))
    if "rua" not in tags:
        issues.append(_issue("warn", "Chýba rua= – nebudete dostávať agregované reporty."))
    if "ruf" not in tags:
        issues.append(_issue("info", "Chýba ruf= – nebudete dostávať forenzné reporty (podporuje ich málo poskytovateľov)."))
    try:
        if int(tags.get("pct", "100")) < 100:
            issues.append(_issue("warn", f"pct={tags['pct']} – politika sa aplikuje iba na časť správ."))
    except ValueError:
        issues.append(_issue("error", "Neplatná hodnota pct=."))
    if p in ("quarantine", "reject") and tags.get("sp", "").lower() == "none":
        issues.append(_issue("warn", "sp=none – subdomény nie sú chránené."))

    # external report destinations must authorize the domain (RFC 7489 §7.1)
    external = []
    for uri in re.split(r",\s*", tags.get("rua", "") + "," + tags.get("ruf", "")):
        m = re.match(r"mailto:[^@]+@([^!,\s]+)", uri.strip(), re.I)
        if not m:
            continue
        dest = m.group(1).lower().rstrip(".")
        if base_domain(dest) != base_domain(domain) and dest not in [e["domain"] for e in external]:
            auth = txt(f"{domain}._report._dmarc.{dest}") or []
            ok = any(a.lower().startswith("v=dmarc1") for a in auth)
            external.append({"domain": dest, "authorized": ok})
            if not ok:
                issues.append(_issue("warn", f"Externý príjemca reportov {dest} nemá autorizačný záznam {domain}._report._dmarc.{dest}."))
    return {"record": record, "tags": tags, "inherited_from": inherited_from, "external_destinations": external,
            "status": _status(issues), "issues": issues}


# -------------------------------------------------------------------- SPF


def _spf_record(domain: str) -> tuple[list[str], bool]:
    res = txt(domain)
    return [r for r in (res or []) if r.lower().startswith("v=spf1")], res is None


def _spf_walk(domain: str, state: dict, depth: int = 0):
    if depth > 10 or domain in state["seen"]:
        return
    state["seen"].add(domain)
    records, nx = _spf_record(domain)
    if not records:
        if depth > 0:
            state["void"] += 1
            state["issues"].append(_issue("error", f"include/redirect na {domain} nemá SPF záznam{' (NXDOMAIN)' if nx else ''}."))
        return
    for term in records[0].split()[1:]:
        t = term.lower().lstrip("+-~?")
        if t.startswith("include:"):
            state["lookups"] += 1
            target = term.split(":", 1)[1]
            state["includes"].append(target)
            _spf_walk(target, state, depth + 1)
        elif t.startswith("redirect="):
            state["lookups"] += 1
            target = term.split("=", 1)[1]
            _spf_walk(target, state, depth + 1)
        elif t in ("a", "mx") or t.startswith(("a:", "a/", "mx:", "mx/", "exists:")):
            state["lookups"] += 1
        elif t == "ptr" or t.startswith("ptr:"):
            state["lookups"] += 1
            state["issues"].append(_issue("warn", f"Mechanizmus ptr ({domain}) je zastaraný (RFC 7208) a pomalý."))


def check_spf(domain: str) -> dict:
    records, _ = _spf_record(domain)
    if not records:
        return {"record": None, "lookups": 0, "includes": [], "status": "error",
                "issues": [_issue("error", "Doména nemá SPF záznam.")]}
    state = {"seen": set(), "lookups": 0, "void": 0, "includes": [], "issues": []}
    if len(records) > 1:
        state["issues"].append(_issue("error", "Viac SPF záznamov – výsledok bude permerror."))
    _spf_walk(domain, state)
    record = records[0]
    issues = state["issues"]
    all_term = next((t.lower() for t in record.split() if t.lower().lstrip("+-~?") == "all"), None)
    if all_term in ("+all", "all"):
        issues.append(_issue("error", "+all povoľuje odosielanie komukoľvek."))
    elif all_term == "?all":
        issues.append(_issue("warn", "?all (neutral) neposkytuje ochranu."))
    elif all_term == "~all":
        issues.append(_issue("info", "~all (softfail) – pri DMARC enforcement je v poriadku, -all je prísnejšie."))
    elif all_term is None and "redirect=" not in record.lower():
        issues.append(_issue("warn", "Chýba koncový mechanizmus all."))
    if state["lookups"] > 10:
        issues.append(_issue("error", f"{state['lookups']} DNS lookupov – limit je 10 (permerror). Zvážte SPF flattening."))
    elif state["lookups"] >= 8:
        issues.append(_issue("warn", f"{state['lookups']} DNS lookupov – blízko limitu 10."))
    if state["void"] > 2:
        issues.append(_issue("error", f"{state['void']} void lookupov – limit je 2."))
    if len(record) > 450:
        issues.append(_issue("info", "Dlhý SPF záznam – riziko UDP fragmentácie."))
    return {"record": record, "lookups": state["lookups"], "includes": state["includes"],
            "all": all_term, "status": _status(issues), "issues": issues}


# ------------------------------------------------------------------- DKIM


def _key_bits(p: str, k: str) -> int | None:
    if k == "ed25519":
        return 256
    try:
        return load_der_public_key(base64.b64decode(p + "=" * (-len(p) % 4))).key_size
    except Exception:  # noqa: BLE001
        return None


def check_dkim(domain: str, selectors: list[str]) -> dict:
    found = []
    tried = list(dict.fromkeys([s for s in selectors if s] + COMMON_DKIM_SELECTORS))
    def lookup(sel):
        try:
            return txt(f"{sel}._domainkey.{domain}")
        except DnsLookupError:
            return False

    with ThreadPoolExecutor(max_workers=10) as pool:
        answers = list(pool.map(lookup, tried))
    if all(a is False for a in answers):
        raise DnsLookupError(f"DKIM lookups for {domain} failed")
    answers = [a or [] for a in answers]
    for sel, records in zip(tried, answers, strict=True):
        rec = next((r for r in (records or []) if "p=" in r), None)
        if not rec:
            continue
        tags = parse_tags(rec)
        p = re.sub(r"\s+", "", tags.get("p", ""))
        k = tags.get("k", "rsa").lower()
        entry = {"selector": sel, "record": rec[:300], "key_type": k, "bits": None, "revoked": p == "",
                 "from_reports": sel in selectors, "testing": "y" in tags.get("t", "")}
        if p:
            entry["bits"] = _key_bits(p, k)
        found.append(entry)
    issues = []
    if not found:
        issues.append(_issue("error" if selectors else "warn",
                             "Nenašiel sa DKIM kľúč (skúšané selektory z reportov a bežné selektory)."))
    for f in found:
        if f["revoked"]:
            issues.append(_issue("info", f"Selektor {f['selector']} je zrušený (prázdne p=)."))
        elif f["bits"] and f["key_type"] == "rsa" and f["bits"] < 1024:
            issues.append(_issue("error", f"Selektor {f['selector']}: RSA {f['bits']} bitov je nebezpečne krátky kľúč."))
        elif f["bits"] and f["key_type"] == "rsa" and f["bits"] < 2048:
            issues.append(_issue("warn", f"Selektor {f['selector']}: RSA {f['bits']} bitov – odporúča sa 2048."))
        if f["testing"]:
            issues.append(_issue("info", f"Selektor {f['selector']} má t=y (testovací režim)."))
    missing = [s for s in selectors if s not in {f["selector"] for f in found}]
    for s in missing:
        issues.append(_issue("warn", f"Selektor {s} je v reportoch, ale v DNS neexistuje."))
    return {"selectors": found, "status": _status(issues, bool(found)), "issues": issues}


# --------------------------------------------------------------------- MX


def check_mx(domain: str) -> dict:
    try:
        ans = _resolver().resolve(domain, "MX")
        hosts = sorted(({"pref": r.preference, "host": str(r.exchange).rstrip(".").lower()} for r in ans),
                       key=lambda h: h["pref"])
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        hosts = []
    except (dns.resolver.NoNameservers, dns.exception.Timeout) as exc:
        raise DnsLookupError(f"{domain} MX: {exc.__class__.__name__}") from exc
    issues = []
    if not hosts:
        issues.append(_issue("info", "Doména nemá MX – neprijíma poštu (zvážte null MX a p=reject)."))
    elif len(hosts) == 1 and hosts[0]["host"] in ("", "."):
        issues.append(_issue("info", "Null MX (RFC 7505) – doména neprijíma poštu."))
    provider = next((identify(h["host"], [], []) for h in hosts if identify(h["host"], [], [])), None)
    return {"hosts": hosts, "provider": provider, "status": "ok" if hosts else "info", "issues": issues}


# ---------------------------------------------------------------- MTA-STS


def check_mta_sts(domain: str, mx_hosts: list[str]) -> dict:
    records = [r for r in (txt(f"_mta-sts.{domain}") or []) if r.lower().startswith("v=stsv1")]
    if not records:
        return {"record": None, "policy": None, "status": "missing",
                "issues": [_issue("info", "MTA-STS nie je nasadené – prenos pošty k vám nie je chránený proti downgrade útokom.")]}
    issues: list[dict] = []
    policy = None
    try:
        r = httpx.get(f"https://mta-sts.{domain}/.well-known/mta-sts.txt", timeout=6, follow_redirects=False)
        if r.status_code == 200:
            policy = {"mx": []}
            for line in r.text.splitlines():
                if ":" in line:
                    k, v = (x.strip() for x in line.split(":", 1))
                    if k == "mx":
                        policy["mx"].append(v)
                    else:
                        policy[k] = v
        else:
            issues.append(_issue("error", f"Politika MTA-STS vrátila HTTP {r.status_code}."))
    except httpx.HTTPError as exc:
        issues.append(_issue("error", f"Politiku MTA-STS sa nepodarilo stiahnuť: {exc.__class__.__name__}."))
    if policy:
        mode = policy.get("mode")
        if mode == "testing":
            issues.append(_issue("warn", "MTA-STS je v režime testing."))
        elif mode == "none":
            issues.append(_issue("warn", "MTA-STS režim none – politika je vypnutá."))
        patterns = policy.get("mx", [])
        for host in mx_hosts:
            if not any(host == p or (p.startswith("*.") and host.endswith(p[1:])) for p in patterns):
                issues.append(_issue("error", f"MX {host} nie je pokrytý MTA-STS politikou."))
    return {"record": records[0], "policy": policy, "status": _status(issues), "issues": issues}


# ---------------------------------------------------------------- TLS-RPT


def check_tls_rpt(domain: str) -> dict:
    records = [r for r in (txt(f"_smtp._tls.{domain}") or []) if r.lower().startswith("v=tlsrptv1")]
    if not records:
        return {"record": None, "status": "missing",
                "issues": [_issue("info", "TLS-RPT nie je nastavené – nedostanete reporty o zlyhaniach TLS.")]}
    return {"record": records[0], "rua": parse_tags(records[0]).get("rua"), "status": "ok", "issues": []}


# ------------------------------------------------------------------- BIMI


def check_bimi(domain: str, dmarc: dict) -> dict:
    records = [r for r in (txt(f"default._bimi.{domain}") or []) if r.lower().startswith("v=bimi1")]
    if not records:
        return {"record": None, "status": "missing",
                "issues": [_issue("info", "BIMI nie je nastavené (logo značky v schránke príjemcu).")]}
    tags = parse_tags(records[0])
    issues = []
    policy = (dmarc.get("tags") or {}).get("p", "").lower()
    if policy not in ("quarantine", "reject"):
        issues.append(_issue("error", "BIMI vyžaduje DMARC p=quarantine alebo p=reject."))
    if not tags.get("a"):
        issues.append(_issue("warn", "Chýba VMC/CMC certifikát (a=) – Gmail a Apple logo nezobrazia."))
    return {"record": records[0], "logo": tags.get("l"), "vmc": tags.get("a"), "status": _status(issues), "issues": issues}


# ------------------------------------------------------------------ score


def score(result: dict) -> int | None:
    if any(result[k]["status"] == "unknown" for k in ("dmarc", "spf", "dkim")):
        return None
    s = 0
    d = result["dmarc"]
    if d["record"]:
        s += 10
        p = (d["tags"].get("sp") if d.get("inherited_from") else None) or d["tags"].get("p", "")
        s += {"reject": 22, "quarantine": 14, "none": 3}.get(p.lower(), 0)
        s += 5 if "rua" in d["tags"] else 0
        s += 3 if d["status"] != "error" and d["tags"].get("pct", "100") == "100" else 0
    sp = result["spf"]
    if sp["record"]:
        s += 10
        s += {"-all": 8, "~all": 6}.get(sp.get("all") or "", 0)
        s += 7 if sp["lookups"] <= 10 and sp["status"] != "error" else 0
    dk = result["dkim"]
    valid = [x for x in dk["selectors"] if not x["revoked"]]
    if valid:
        s += 15
        s += 5 if all((x["bits"] or 2048) >= 2048 for x in valid) else 2
    s += 7 if result["mta_sts"]["status"] == "ok" else (3 if result["mta_sts"]["record"] else 0)
    s += 4 if result["tls_rpt"]["record"] else 0
    s += 4 if result["bimi"]["status"] == "ok" else (2 if result["bimi"]["record"] else 0)
    return min(s, 100)


def grade(value: int | None) -> str:
    if value is None:
        return "?"
    return "A" if value >= 90 else "B" if value >= 75 else "C" if value >= 60 else "D" if value >= 40 else "F"


UNKNOWN_SHAPES = {
    "dmarc": {"record": None, "tags": {}, "external_destinations": []},
    "spf": {"record": None, "lookups": 0, "includes": []},
    "dkim": {"selectors": []},
    "mx": {"hosts": [], "provider": None},
    "mta_sts": {"record": None, "policy": None},
    "tls_rpt": {"record": None},
    "bimi": {"record": None},
}


def _safe(kind: str, fn, *args) -> dict:
    """Run one check; a resolver failure yields an explicit 'unknown' result instead of 'missing'."""
    try:
        return fn(*args)
    except DnsLookupError as exc:
        return {**UNKNOWN_SHAPES[kind], "status": "unknown", "issues": [_issue(
            "warn", f"DNS server neodpovedal ({exc}) – výsledok sa nedá určiť. Skontrolujte DNS_SERVERS.")]}


def check_domain(domain: str, dkim_selectors: list[str] | None = None) -> dict:
    domain = domain.lower().strip().rstrip(".")
    with ThreadPoolExecutor(max_workers=5) as pool:
        f_dmarc = pool.submit(_safe, "dmarc", check_dmarc, domain)
        f_spf = pool.submit(_safe, "spf", check_spf, domain)
        f_dkim = pool.submit(_safe, "dkim", check_dkim, domain, dkim_selectors or [])
        f_mx = pool.submit(_safe, "mx", check_mx, domain)
        f_tls = pool.submit(_safe, "tls_rpt", check_tls_rpt, domain)
        dmarc, mx = f_dmarc.result(), f_mx.result()
        f_sts = pool.submit(_safe, "mta_sts", check_mta_sts, domain, [h["host"] for h in mx["hosts"]])
        f_bimi = pool.submit(_safe, "bimi", check_bimi, domain, dmarc)
        result = {
            "domain": domain,
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "resolvers": configured_servers() or ["system"],
            "dmarc": dmarc,
            "spf": f_spf.result(),
            "dkim": f_dkim.result(),
            "mx": mx,
            "mta_sts": f_sts.result(),
            "tls_rpt": f_tls.result(),
            "bimi": f_bimi.result(),
        }
    result["score"] = score(result)
    result["grade"] = grade(result["score"])
    return result
