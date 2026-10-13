"""Exchange Online mailbox reader via Microsoft Graph (app-only, client credentials)."""

import logging
import time
from collections.abc import Iterator
from urllib.parse import quote

import httpx
import msal

log = logging.getLogger(__name__)


class GraphError(Exception):
    pass


class GraphMailbox:
    """
    Required config keys: tenant_id, client_id, mailbox and client_secret or (cert_pem + cert_thumbprint).
    Optional: folder (default "Inbox", path like "Inbox/DMARC"), processed_folder, failed_folder,
              only_unread (bool), mark_read (bool), authority_host, graph_endpoint.
    Application permission needed: Mail.ReadWrite (or Mail.Read when neither moving nor marking read),
    ideally scoped to the report mailbox with Exchange RBAC for Applications.
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.mailbox = quote(cfg["mailbox"])
        self.graph = cfg.get("graph_endpoint") or "https://graph.microsoft.com"
        authority = (cfg.get("authority_host") or "https://login.microsoftonline.com").rstrip("/")
        if cfg.get("cert_pem"):
            credential = {"private_key": cfg["cert_pem"], "thumbprint": cfg.get("cert_thumbprint", "")}
        else:
            credential = cfg.get("client_secret", "")
        self.app = msal.ConfidentialClientApplication(
            cfg["client_id"], authority=f"{authority}/{cfg['tenant_id']}", client_credential=credential
        )
        self.http = httpx.Client(timeout=60)
        self._folders: dict[str, str] = {}

    # ---------------------------------------------------------- plumbing

    def _token(self) -> str:
        res = self.app.acquire_token_for_client(scopes=[f"{self.graph}/.default"])
        if "access_token" not in res:
            raise GraphError(f"Token: {res.get('error')}: {res.get('error_description', '')[:300]}")
        return res["access_token"]

    def _req(self, method: str, path: str, **kw) -> httpx.Response:
        url = path if path.startswith("http") else f"{self.graph}/v1.0/users/{self.mailbox}{path}"
        for attempt in range(5):
            r = self.http.request(method, url, headers={"Authorization": f"Bearer {self._token()}"}, **kw)
            if r.status_code in (429, 503, 504):
                wait = int(r.headers.get("Retry-After", 2 ** attempt))
                log.info("Graph throttled, waiting %ss", wait)
                time.sleep(min(wait, 60))
                continue
            if r.status_code >= 400:
                try:
                    err = r.json().get("error", {})
                    msg = f"{err.get('code')}: {err.get('message')}"
                except ValueError:
                    msg = r.text[:300]
                raise GraphError(f"HTTP {r.status_code} {msg}")
            return r
        raise GraphError("Graph API throttling – vyčerpané opakovania")

    def resolve_folder(self, path: str, create: bool = False) -> str:
        path = (path or "Inbox").strip("/")
        if path in self._folders:
            return self._folders[path]
        parts = path.split("/")
        parent = None
        for i, name in enumerate(parts):
            if i == 0 and name.lower() in ("inbox", "archive", "junkemail", "deleteditems", "sentitems"):
                parent = self._req("GET", f"/mailFolders/{name.lower()}").json()["id"]
                continue
            base = f"/mailFolders/{parent}/childFolders" if parent else "/mailFolders"
            safe = name.replace("'", "''")
            found = self._req("GET", base, params={"$filter": f"displayName eq '{safe}'", "$select": "id"}).json()["value"]
            if found:
                parent = found[0]["id"]
            elif create:
                parent = self._req("POST", base, json={"displayName": name}).json()["id"]
            else:
                raise GraphError(f"Priečinok '{path}' neexistuje")
        self._folders[path] = parent
        return parent

    # ------------------------------------------------------------- API

    def test(self) -> str:
        fid = self.resolve_folder(self.cfg.get("folder") or "Inbox")
        info = self._req("GET", f"/mailFolders/{fid}", params={"$select": "displayName,totalItemCount,unreadItemCount"}).json()
        return f"OK – priečinok {info['displayName']}: {info['totalItemCount']} správ, {info['unreadItemCount']} neprečítaných"

    def fetch(self, limit: int = 500) -> Iterator[tuple[str, str, bytes]]:
        fid = self.resolve_folder(self.cfg.get("folder") or "Inbox")
        params = {"$select": "id,subject,receivedDateTime", "$top": "50"}
        if self.cfg.get("only_unread", True):
            params["$filter"] = "isRead eq false"
        ids: list[tuple[str, str]] = []
        url: str | None = f"/mailFolders/{fid}/messages"
        while url and len(ids) < limit:
            data = self._req("GET", url, params=params if not url.startswith("http") else None).json()
            ids.extend((m["id"], m.get("subject") or "") for m in data.get("value", []))
            url = data.get("@odata.nextLink")
        for mid, subject in ids[:limit]:
            raw = self._req("GET", f"/messages/{mid}/$value").content
            yield mid, subject, raw

    def done(self, message_id: str, ok: bool):
        target = self.cfg.get("processed_folder") if ok else (self.cfg.get("failed_folder") or None)
        if self.cfg.get("mark_read", True):
            self._req("PATCH", f"/messages/{message_id}", json={"isRead": True})
        if target:
            dest = self.resolve_folder(target, create=True)
            self._req("POST", f"/messages/{message_id}/move", json={"destinationId": dest})

    def close(self):
        self.http.close()
