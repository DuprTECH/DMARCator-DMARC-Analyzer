"""Validation shared by the mailbox form and destructive cleanup."""


def retention_days(cfg: dict) -> int:
    if not cfg.get("cleanup_enabled"):
        return 0
    raw = str(cfg.get("cleanup_days", ""))
    if not raw.isdecimal() or not 1 <= int(raw) <= 3650:
        raise ValueError("Retencia musí byť celé číslo od 1 do 3650 dní.")
    processed = (cfg.get("processed_folder") or "").strip("/").casefold()
    protected = {
        (cfg.get("folder") or "Inbox").strip("/").casefold(),
        (cfg.get("failed_folder") or "").strip("/").casefold(),
        "inbox", "archive", "deleteditems", "sentitems", "junkemail",
    }
    if not processed or processed in protected:
        raise ValueError("Čistenie vyžaduje samostatný priečinok spracovaných reportov.")
    if cfg.get("graph_endpoint", "").rstrip("/") not in ("", "https://graph.microsoft.com"):
        raise ValueError("Automatické čistenie je podporované iba v globálnom Microsoft Graph.")
    return int(raw)
