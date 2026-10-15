from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.ingest.graph import PROCESSED_PROPERTY, GraphError, GraphMailbox
from app.ingest.retention import retention_days


def mailbox(**overrides):
    box = GraphMailbox.__new__(GraphMailbox)
    box.cfg = {
        "cleanup_enabled": True, "cleanup_days": 30, "folder": "Inbox",
        "processed_folder": "Inbox/processed", "failed_folder": "Inbox/error",
        **overrides,
    }
    box.resolve_folder = lambda path, **kw: path.casefold().replace("/", "-")
    return box


def message(mid, days=31, marked=True):
    return {
        "id": mid,
        "receivedDateTime": (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(),
        "singleValueExtendedProperties": [{"id": PROCESSED_PROPERTY, "value": "true"}] if marked else [],
    }


def test_cleanup_disabled_makes_no_requests():
    box = mailbox(cleanup_enabled=False)
    assert box.cleanup() == 0


@pytest.mark.parametrize("changes", [
    {"cleanup_days": 0}, {"cleanup_days": -1}, {"cleanup_days": 3651},
    {"cleanup_days": "abc"}, {"cleanup_days": "1.5"}, {"cleanup_days": True},
    {"processed_folder": ""}, {"processed_folder": "Inbox"},
    {"processed_folder": "INBOX/error"}, {"processed_folder": "DeletedItems"},
    {"graph_endpoint": "https://graph.microsoft.us"},
])
def test_unsafe_configuration_rejected(changes):
    with pytest.raises(ValueError):
        retention_days(mailbox(**changes).cfg)


def test_cleanup_checks_resolved_folder_identity():
    box = mailbox()
    box.resolve_folder = lambda *a, **kw: "same-id"
    with pytest.raises(GraphError):
        box.cleanup()


def test_cleanup_pages_before_deleting_and_uses_permanent_delete():
    box = mailbox()
    calls = []

    def request(method, path, **kw):
        calls.append((method, path, kw))
        if method == "GET":
            if path.startswith("http"):
                return SimpleNamespace(json=lambda: {"value": [message("second")]})
            return SimpleNamespace(json=lambda: {
                "value": [message("first")],
                "@odata.nextLink": "https://graph.microsoft.com/next-page",
            })
        return None

    box._req = request
    assert box.cleanup() == 2
    assert [c[0] for c in calls] == ["GET", "GET", "POST", "POST"]
    assert calls[0][2]["params"]["$orderby"] == "receivedDateTime asc"
    assert "receivedDateTime lt" in calls[0][2]["params"]["$filter"]
    assert PROCESSED_PROPERTY in calls[0][2]["params"]["$filter"]
    assert calls[1][2]["params"] is None
    assert calls[2][1] == "/mailFolders/inbox-processed/messages/first/permanentDelete"


@pytest.mark.parametrize("item", [message("unmarked", marked=False), message("recent", days=1)])
def test_cleanup_refuses_messages_outside_policy(item):
    box = mailbox()
    calls = []

    def request(method, path, **kw):
        calls.append(method)
        return SimpleNamespace(json=lambda: {"value": [item]})

    box._req = request
    with pytest.raises(GraphError):
        box.cleanup()
    assert calls == ["GET"]


def test_cleanup_limit_and_delete_failure():
    box = mailbox()
    calls = []

    def request(method, path, **kw):
        calls.append(method)
        if method == "GET":
            return SimpleNamespace(json=lambda: {"value": [message(str(i)) for i in range(501)]})
        return None

    box._req = request
    assert box.cleanup() == 500
    assert calls.count("POST") == 500

    def failing(method, path, **kw):
        if method == "GET":
            return SimpleNamespace(json=lambda: {"value": [message("old")]})
        raise GraphError("Access denied")

    box._req = failing
    with pytest.raises(GraphError, match="Access denied"):
        box.cleanup()


def test_only_successful_reports_are_marked():
    box = mailbox(mark_read=False)
    calls = []
    box._req = lambda method, path, **kw: calls.append((method, path, kw))
    box.done("bad", "failed")
    assert [c[0] for c in calls] == ["POST"]
    assert calls[0][2]["json"]["destinationId"] == "inbox-error"
    calls.clear()
    box.done("good", "ok")
    assert calls[0][0] == "PATCH"
    assert calls[0][2]["json"] == {
        "singleValueExtendedProperties": [{"id": PROCESSED_PROPERTY, "value": "true"}],
    }


def test_target_folders():
    from app.ingest.retention import target_folder

    cfg = {"processed_folder": "Inbox/Done", "failed_folder": "Inbox/Error"}
    assert target_folder(cfg, "ok") == "Inbox/Done"
    assert target_folder(cfg, "failed") == "Inbox/Error"
    assert target_folder(cfg, "noreport") == "Inbox/Error"  # falls back to the failed folder
    assert target_folder({**cfg, "noreport_folder": "Inbox/No report"}, "noreport") == "Inbox/No report"
    assert target_folder({}, "noreport") is None
