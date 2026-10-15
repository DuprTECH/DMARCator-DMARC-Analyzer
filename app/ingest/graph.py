"""Exchange Online mailbox reader via Microsoft Graph (app-only, client credentials)."""

import logging
import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx
import msal

from .retention import retention_days, target_folder

log = logging.getLogger(__name__)
# keeps the pre-rename name: messages already marked in mailboxes must still be recognised
PROCESSED_PROPERTY = "String {c3737793-56f7-47c0-a20a-7f90a5b05832} Name DMARCulaProcessed"


class GraphError(Exception):
    pass


class GraphMailbox:
    """
    Required config keys: tenant_id, client_id, mailbox and client_secret or (cert_pem + cert_thumbprint).
    Optional: folder (default "Inbox", path like "Inbox/DMARC"), processed_folder, failed_folder, noreport_folder,
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

    def done(self, message_id: str, outcome: str):
        """outcome: "ok" (report stored), "noreport" (no report in the message) or "failed"."""
        ok = outcome == "ok"
        target = target_folder(self.cfg, outcome)
        patch = {}
        if self.cfg.get("mark_read", True):
            patch["isRead"] = True
        if ok and retention_days(self.cfg):
            patch["singleValueExtendedProperties"] = [{"id": PROCESSED_PROPERTY, "value": "true"}]
        if patch:
            self._req("PATCH", f"/messages/{message_id}", json=patch)
        if target:
            dest = self.resolve_folder(target, create=True)
            self._req("POST", f"/messages/{message_id}/move", json={"destinationId": dest})

    def cleanup(self) -> int:
        days = retention_days(self.cfg)
        if not days:
            return 0
        folder = self.resolve_folder(self.cfg["processed_folder"])
        protected = [self.cfg.get("folder") or "Inbox", "Inbox", "DeletedItems", "SentItems", "JunkEmail", "Archive"]
        protected += [self.cfg[k] for k in ("failed_folder", "noreport_folder") if self.cfg.get(k)]
        if folder in {self.resolve_folder(path) for path in protected}:
            raise GraphError("Čistenie odmietnuté: priečinok spracovaných nie je samostatný.")
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        stamp = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
        params = {
            "$select": "id,receivedDateTime",
            "$filter": f"receivedDateTime lt {stamp} and singleValueExtendedProperties/Any("
                       f"p: p/id eq '{PROCESSED_PROPERTY}' and p/value eq 'true')",
            "$expand": f"singleValueExtendedProperties($filter=id eq '{PROCESSED_PROPERTY}')",
            "$orderby": "receivedDateTime asc",
            "$top": "100",
        }
        # Collect before deleting so pagination cannot skip messages as the folder changes.
        candidates = []
        url = f"/mailFolders/{folder}/messages"
        while url and len(candidates) < 500:
            data = self._req("GET", url, params=params if not url.startswith("http") else None).json()
            for item in data["value"]:
                marked = any(p["id"] == PROCESSED_PROPERTY and p["value"] == "true"
                             for p in item.get("singleValueExtendedProperties", []))
                received = datetime.fromisoformat(item["receivedDateTime"].replace("Z", "+00:00"))
                if not marked or received >= cutoff:
                    raise GraphError("Čistenie odmietnuté: server vrátil správu mimo pravidiel retencie.")
                candidates.append(item["id"])
                if len(candidates) == 500:
                    break
            url = data.get("@odata.nextLink")
        deleted = 0
        for mid in candidates:
            # Folder-scoped endpoint also protects messages moved out since listing.
            self._req("POST", f"/mailFolders/{folder}/messages/{mid}/permanentDelete")
            deleted += 1
            log.info("Retention permanently deleted processed message %s from %s", mid, self.cfg["processed_folder"])
        return deleted

    def close(self):
        self.http.close()
