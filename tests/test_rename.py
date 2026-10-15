from app.config import Settings


def test_legacy_app_name_is_shown_with_new_name():
    assert Settings(app_name="DMARCula").app_name == "UnDMARC"
    assert Settings(app_name="Our DMARC portal").app_name == "Our DMARC portal"


def test_legacy_sqlite_file_is_kept(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    default = "sqlite:///./data/undmarc.db"
    assert Settings(database_url=default).database_url == default

    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "dmarcula.db").write_bytes(b"")
    assert Settings(database_url=default).database_url == "sqlite:///./data/dmarcula.db"

    (tmp_path / "data" / "undmarc.db").write_bytes(b"")
    assert Settings(database_url=default).database_url == default
