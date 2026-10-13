import os
from contextlib import contextmanager

from sqlalchemy import create_engine, event, inspect, make_url, select, update
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings

settings = get_settings()

if settings.database_url.startswith("sqlite:///"):
    path = settings.database_url.removeprefix("sqlite:///")
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "connect")
    def _sqlite_connect(dbapi_conn, _):
        # let SQLAlchemy own transactions so SAVEPOINTs work with pysqlite
        dbapi_conn.isolation_level = None
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    @event.listens_for(engine, "begin")
    def _sqlite_begin(conn):
        conn.exec_driver_sql("BEGIN")
elif settings.database_url.startswith(("mysql", "mariadb")):
    url = make_url(settings.database_url)
    if "charset" not in url.query:
        url = url.update_query_dict({"charset": "utf8mb4"})
    # recycle before MariaDB's wait_timeout drops idle connections
    engine = create_engine(url, pool_pre_ping=True, pool_recycle=1800)
else:
    engine = create_engine(settings.database_url, pool_pre_ping=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _add_missing_columns() -> set[tuple[str, str]]:
    """Lightweight forward migration: add nullable columns introduced by newer versions."""
    insp = inspect(engine)
    quote = engine.dialect.identifier_preparer.quote
    added = set()
    for table in Base.metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        existing = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name not in existing and col.nullable:
                with engine.begin() as conn:
                    conn.exec_driver_sql(
                        f"ALTER TABLE {quote(table.name)} ADD COLUMN {quote(col.name)} {col.type.compile(engine.dialect)}"
                    )
                added.add((table.name, col.name))
    return added


def _backfill_first_results():
    from .ingest.service import first_results
    from .models import AggregateRecord as R

    last_id = 0
    while True:
        with session_scope() as db:
            rows = db.execute(
                select(R.id, R.dkim_results, R.spf_results).where(R.id > last_id).order_by(R.id).limit(5000)
            ).all()
            if not rows:
                return
            db.execute(update(R), [{"id": rid, **first_results(d, s)} for rid, d, s in rows])
            last_id = rows[-1][0]


def init_db():
    from . import models  # noqa: F401

    Base.metadata.create_all(engine)
    added = _add_missing_columns()
    # create_all skips existing tables – add indexes introduced by newer versions
    for table in Base.metadata.sorted_tables:
        for index in table.indexes:
            index.create(engine, checkfirst=True)
    if ("aggregate_records", "dkim_result") in added:
        _backfill_first_results()
