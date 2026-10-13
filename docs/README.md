# DMARCula documentation

| Document | Audience | Contents |
|---|---|---|
| [Installation](installation.md) | server admin | step-by-step install on Debian/Ubuntu, RHEL/Rocky/Alma and other distros, MariaDB, nginx/Apache, hosting panels, Docker, updates, backups, troubleshooting |
| [Configuration](configuration.md) | server admin | every environment variable, database URLs, mailbox settings |
| [Authentication](authentication.md) | identity admin | local accounts, LDAP / Active Directory, SSO with Entra ID and other OIDC providers, roles |
| [Microsoft 365 setup](microsoft-365.md) | M365 / DNS admin | report mailbox, app registration, scoping to one mailbox, IMAP OAuth2, DNS records, DKIM |
| [User guide](user-guide.md) | security / mail team | dashboard, traffic categories, DNS score, policy readiness, rollout from `p=none` to `p=reject` |
| [Architecture](architecture.md) | developers | components, data flow, data model, performance, security, HTTP interface, extending |

## DMARC in one minute

**DMARC** (RFC 7489) tells receiving mail servers what to do with mail that claims to come
from your domain but fails authentication:

- **SPF** checks that the sending IP may send for the envelope (MAIL FROM) domain.
- **DKIM** checks a cryptographic signature on the message.
- **Alignment** means the SPF or DKIM domain matches the domain in the visible `From:` header.
  DMARC passes when **at least one** of them passes *and* is aligned.
- **Policy** `p=none` only monitors, `p=quarantine` sends failing mail to spam, and `p=reject`
  refuses it.

Receivers (Google, Microsoft, Yahoo, …) send daily **aggregate reports** to the `rua=` address.
DMARCula collects and analyzes them. It shows who sends mail as your domains and when each
domain is ready for a stricter policy.
