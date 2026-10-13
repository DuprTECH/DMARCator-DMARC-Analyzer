"""Generic IMAP reader – password auth or OAuth2 (XOAUTH2) for Exchange Online."""

import imaplib
import re
import ssl
from collections.abc import Iterator

import msal


class ImapError(Exception):
    pass


def _quote(folder: str) -> str:
    return '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'


class ImapMailbox:
    """
    Config keys: host, port (993), security (ssl|starttls|none), username,
    auth (password|oauth2_m365), password  –or–  tenant_id, client_id, client_secret,
    folder (INBOX), processed_folder, failed_folder, only_unread, mark_read, tls_verify.
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.conn: imaplib.IMAP4 | None = None

    def _connect(self):
        c = self.cfg
        ctx = ssl.create_default_context()
        if not c.get("tls_verify", True):
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        port = int(c.get("port") or 993)
        security = c.get("security", "ssl")
        if security == "ssl":
            conn = imaplib.IMAP4_SSL(c["host"], port, ssl_context=ctx, timeout=60)
        else:
            conn = imaplib.IMAP4(c["host"], port, timeout=60)
            if security == "starttls":
                conn.starttls(ssl_context=ctx)
        if c.get("auth") == "oauth2_m365":
            app = msal.ConfidentialClientApplication(
                c["client_id"],
                authority=f"https://login.microsoftonline.com/{c['tenant_id']}",
                client_credential=c["client_secret"],
            )
            res = app.acquire_token_for_client(scopes=["https://outlook.office365.com/.default"])
            if "access_token" not in res:
                raise ImapError(f"Token: {res.get('error')}: {res.get('error_description', '')[:300]}")
            auth = f"user={c['username']}\x01auth=Bearer {res['access_token']}\x01\x01"
            conn.authenticate("XOAUTH2", lambda _: auth.encode())
        else:
            conn.login(c["username"], c["password"])
        self.conn = conn
        return conn

    def _select(self, folder: str):
        typ, data = self.conn.select(_quote(folder))
        if typ != "OK":
            raise ImapError(f"Priečinok '{folder}' neexistuje: {data}")
        return int(data[0] or 0)

    def test(self) -> str:
        try:
            self._connect()
            count = self._select(self.cfg.get("folder") or "INBOX")
            return f"OK – priečinok {self.cfg.get('folder') or 'INBOX'}: {count} správ"
        except imaplib.IMAP4.error as exc:
            raise ImapError(str(exc)) from exc

    def fetch(self, limit: int = 500) -> Iterator[tuple[str, str, bytes]]:
        try:
            self._connect()
            self._select(self.cfg.get("folder") or "INBOX")
            crit = "UNSEEN" if self.cfg.get("only_unread", True) else "ALL"
            typ, data = self.conn.uid("SEARCH", None, crit)
            uids = (data[0] or b"").split()[:limit]
            for uid in uids:
                typ, msg = self.conn.uid("FETCH", uid, "(BODY.PEEK[])")
                raw = next((part[1] for part in msg if isinstance(part, tuple)), None)
                if raw:
                    m = re.search(rb"^Subject:\s*(.*)$", raw[:20000], re.M | re.I)
                    yield uid.decode(), (m.group(1).decode(errors="replace").strip() if m else ""), raw
        except imaplib.IMAP4.error as exc:
            raise ImapError(str(exc)) from exc

    def done(self, uid: str, ok: bool):
        target = self.cfg.get("processed_folder") if ok else self.cfg.get("failed_folder")
        if self.cfg.get("mark_read", True):
            self.conn.uid("STORE", uid, "+FLAGS", "(\\Seen)")
        if target:
            self.conn.create(_quote(target))  # no-op error if it exists
            if "MOVE" in self.conn.capabilities:
                self.conn.uid("MOVE", uid, _quote(target))
            else:
                self.conn.uid("COPY", uid, _quote(target))
                self.conn.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
                self.conn.expunge()

    def close(self):
        if self.conn:
            try:
                self.conn.logout()
            except Exception:  # noqa: BLE001
                pass
