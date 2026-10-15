"""Folder rules shared by the mailbox form, both mailbox types and destructive cleanup."""


def target_folder(cfg: dict, outcome: str) -> str | None:
    """Folder a message is moved to: "ok" → processed, "noreport" → without-report folder (falls back
    to the failed folder), "failed" → failed folder. None = leave the message where it is."""
    if outcome == "ok":
        return cfg.get("processed_folder") or None
    if outcome == "noreport":
        return cfg.get("noreport_folder") or cfg.get("failed_folder") or None
    return cfg.get("failed_folder") or None


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
        (cfg.get("noreport_folder") or "").strip("/").casefold(),
        "inbox", "archive", "deleteditems", "sentitems", "junkemail",
    }
    if not processed or processed in protected:
        raise ValueError("Čistenie vyžaduje samostatný priečinok spracovaných reportov.")
    if cfg.get("graph_endpoint", "").rstrip("/") not in ("", "https://graph.microsoft.com"):
        raise ValueError("Automatické čistenie je podporované iba v globálnom Microsoft Graph.")
    return int(raw)
