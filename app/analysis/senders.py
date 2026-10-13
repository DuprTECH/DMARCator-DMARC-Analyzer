"""Identify well-known sending services and classify traffic (dmarcian-style)."""

from publicsuffixlist import PublicSuffixList

_psl = PublicSuffixList()

# base domain (from PTR / DKIM d= / SPF domain) -> service name
KNOWN_SENDERS: dict[str, str] = {
    "outlook.com": "Microsoft 365",
    "office365.com": "Microsoft 365",
    "protection.outlook.com": "Microsoft 365",
    "onmicrosoft.com": "Microsoft 365",
    "hotmail.com": "Microsoft 365",
    "microsoft.com": "Microsoft",
    "google.com": "Google Workspace",
    "googlemail.com": "Google Workspace",
    "gmail.com": "Google Workspace",
    "1e100.net": "Google Workspace",
    "amazonses.com": "Amazon SES",
    "amazonaws.com": "Amazon AWS",
    "sendgrid.net": "SendGrid",
    "sendgrid.com": "SendGrid",
    "mcsv.net": "Mailchimp",
    "mcdlv.net": "Mailchimp",
    "rsgsv.net": "Mailchimp",
    "mailchimpapp.net": "Mailchimp",
    "mandrillapp.com": "Mandrill",
    "mailgun.net": "Mailgun",
    "mailgun.org": "Mailgun",
    "sparkpostmail.com": "SparkPost",
    "postmarkapp.com": "Postmark",
    "mtasv.net": "Postmark",
    "exacttarget.com": "Salesforce Marketing Cloud",
    "salesforce.com": "Salesforce",
    "zendesk.com": "Zendesk",
    "freshdesk.com": "Freshdesk",
    "hubspotemail.net": "HubSpot",
    "hubspot.com": "HubSpot",
    "hs-analytics.net": "HubSpot",
    "sendinblue.com": "Brevo",
    "brevo.com": "Brevo",
    "mailjet.com": "Mailjet",
    "smtp2go.com": "SMTP2GO",
    "zoho.com": "Zoho Mail",
    "zoho.eu": "Zoho Mail",
    "yahoo.com": "Yahoo",
    "yahoodns.net": "Yahoo",
    "icloud.com": "Apple iCloud",
    "apple.com": "Apple iCloud",
    "mimecast.com": "Mimecast",
    "pphosted.com": "Proofpoint",
    "ppe-hosted.com": "Proofpoint",
    "messagelabs.com": "Broadcom Email Security",
    "barracudanetworks.com": "Barracuda",
    "cudasvc.com": "Barracuda",
    "iphmx.com": "Cisco Secure Email",
    "trendmicro.com": "Trend Micro",
    "tmes.trendmicro.eu": "Trend Micro",
    "hornetsecurity.com": "Hornetsecurity",
    "everycloudtech.com": "Hornetsecurity",
    "seznam.cz": "Seznam",
    "atlassian.net": "Atlassian",
    "atlassian.com": "Atlassian",
    "servicenow.com": "ServiceNow",
    "docusign.net": "DocuSign",
    "intercom-mail.com": "Intercom",
    "klaviyomail.com": "Klaviyo",
    "constantcontact.com": "Constant Contact",
    "ccsend.com": "Constant Contact",
    "campaign-archive.com": "Mailchimp",
    "ecomailapp.cz": "Ecomail",
    "smartemailing.cz": "SmartEmailing",
    "websupport.sk": "Websupport",
    "forpsi.com": "Forpsi",
    "active24.com": "Active24",
    "ovh.net": "OVHcloud",
    "ionos.com": "IONOS",
    "secureserver.net": "GoDaddy",
}

MAILBOX_PROVIDERS = {"Microsoft 365", "Google Workspace", "Yahoo", "Apple iCloud", "Seznam", "Zoho Mail"}
SECURITY_GATEWAYS = {
    "Mimecast",
    "Proofpoint",
    "Broadcom Email Security",
    "Barracuda",
    "Cisco Secure Email",
    "Trend Micro",
    "Hornetsecurity",
}

CATEGORIES = {
    "compliant": "V súlade",
    "forwarded": "Preposlané",
    "noncompliant": "Nesúlad (legitímny zdroj)",
    "threat": "Hrozba / neznámy",
}


def base_domain(host: str | None) -> str | None:
    if not host:
        return None
    host = host.lower().rstrip(".")
    try:
        return _psl.privatesuffix(host) or host
    except Exception:  # noqa: BLE001
        return host


def identify(ptr: str | None, dkim_domains: list[str], spf_domains: list[str], asn_org: str | None = None) -> str | None:
    candidates = [ptr] + list(dkim_domains) + list(spf_domains)
    for host in candidates:
        if not host:
            continue
        host = host.lower().rstrip(".")
        # longest suffix match first (e.g. protection.outlook.com)
        parts = host.split(".")
        for i in range(len(parts) - 1):
            suffix = ".".join(parts[i:])
            if suffix in KNOWN_SENDERS:
                return KNOWN_SENDERS[suffix]
    if asn_org:
        a = asn_org.lower()
        for needle, name in (("microsoft", "Microsoft 365"), ("google", "Google Workspace"), ("amazon", "Amazon AWS")):
            if needle in a:
                return name
    return None


def categorize(rec: dict, sender: str | None) -> str:
    """Classify a record like dmarcian / Valimail do."""
    if rec["dmarc_pass"]:
        return "compliant"
    dkim_pass_any = any(d.get("result") == "pass" for d in rec.get("dkim_results") or [])
    reasons = {(r.get("type") or "").lower() for r in rec.get("reasons") or []}
    if reasons & {"forwarded", "mailing_list", "trusted_forwarder", "local_policy"}:
        return "forwarded"
    if any((d.get("domain") or "").endswith(".onmicrosoft.com") for d in rec.get("dkim_results") or []):
        # M365 tenant signing with the default onmicrosoft.com key -> custom DKIM not enabled
        return "noncompliant"
    if sender in MAILBOX_PROVIDERS or sender in SECURITY_GATEWAYS:
        # unaligned DKIM pass via a mailbox provider / gateway is typical for forwarding
        if dkim_pass_any or sender in SECURITY_GATEWAYS:
            return "forwarded"
    if sender:
        return "noncompliant"
    if dkim_pass_any:
        return "forwarded"
    return "threat"
