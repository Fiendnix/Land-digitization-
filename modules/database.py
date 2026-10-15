import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .models import LandRecord

DEFAULT_DATABASE_PATH = Path("data") / "land_records.sqlite3"

_CREATE_TABLES = (
    """
    CREATE TABLE IF NOT EXISTS land_records (
        record_id TEXT PRIMARY KEY,
        parcel_id TEXT,
        survey_number TEXT,
        owner_name TEXT,
        village TEXT,
        taluka TEXT,
        district TEXT,
        state TEXT,
        area REAL,
        area_unit TEXT,
        land_use TEXT,
        document_path TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS validation_results (
        validation_id TEXT PRIMARY KEY,
        record_id TEXT NOT NULL,
        validation_type TEXT NOT NULL,
        status TEXT NOT NULL
            CHECK (status IN ('passed', 'failed', 'needs_review')),
        details_json TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (record_id) REFERENCES land_records(record_id)
            ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS alerts (
        alert_id TEXT PRIMARY KEY,
        record_id TEXT NOT NULL,
        severity TEXT NOT NULL
            CHECK (severity IN ('low', 'medium', 'high', 'critical')),
        message TEXT NOT NULL,
        is_resolved INTEGER NOT NULL DEFAULT 0
            CHECK (is_resolved IN (0, 1)),
        created_at TEXT NOT NULL,
        FOREIGN KEY (record_id) REFERENCES land_records(record_id)
            ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_events (
        event_id TEXT PRIMARY KEY,
        record_id TEXT,
        event_type TEXT NOT NULL,
        actor_id TEXT,
        details_json TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (record_id) REFERENCES land_records(record_id)
            ON DELETE SET NULL
    )
    """,
)


@contextmanager
def _connection(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize_database(db_path: str | Path = DEFAULT_DATABASE_PATH) -> None:
    path = Path(db_path)
    if str(db_path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)

    with _connection(db_path) as connection:
        for statement in _CREATE_TABLES:
            connection.execute(statement)


def create_record(
    record: LandRecord,
    db_path: str | Path = DEFAULT_DATABASE_PATH,
) -> LandRecord:
    with _connection(db_path) as connection:
        connection.execute(
            """
            INSERT INTO land_records (
                record_id, parcel_id, survey_number, owner_name, village,
                taluka, district, state, area, area_unit, land_use,
                document_path, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.record_id,
                record.parcel_id,
                record.survey_number,
                record.owner_name,
                record.village,
                record.taluka,
                record.district,
                record.state,
                record.area,
                record.area_unit,
                record.land_use,
                record.document_path,
                record.created_at,
                record.updated_at,
            ),
        )
    return record


def get_record(
    record_id: str,
    db_path: str | Path = DEFAULT_DATABASE_PATH,
) -> LandRecord | None:
    with _connection(db_path) as connection:
        row = connection.execute(
            "SELECT * FROM land_records WHERE record_id = ?",
            (record_id,),
        ).fetchone()

    if row is None:
        return None
    return LandRecord(**dict(row))


def list_records(db_path: str | Path = DEFAULT_DATABASE_PATH) -> list[LandRecord]:
    with _connection(db_path) as connection:
        rows = connection.execute(
            "SELECT * FROM land_records ORDER BY created_at, record_id"
        ).fetchall()
    return [LandRecord(**dict(row)) for row in rows]
