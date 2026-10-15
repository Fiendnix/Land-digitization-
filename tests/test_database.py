import sqlite3
from datetime import datetime

from modules.database import (
    create_record,
    get_record,
    initialize_database,
    list_records,
)
from modules.models import LandRecord


def test_initialize_database_creates_all_tables_and_can_be_repeated(tmp_path):
    db_path = tmp_path / "nested" / "test.sqlite3"

    initialize_database(db_path)
    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = ?",
                ("table",),
            )
        }

    assert {
        "land_records",
        "validation_results",
        "alerts",
        "audit_events",
    }.issubset(tables)


def test_create_record_persists_record_with_optional_fields_as_none(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    initialize_database(db_path)
    record = LandRecord(parcel_id="P-1", survey_number="S-1")

    created = create_record(record, db_path)
    retrieved = get_record(record.record_id, db_path)

    assert created is record
    assert retrieved == record
    assert retrieved.owner_name is None
    assert retrieved.area is None
    assert retrieved.record_id
    assert datetime.fromisoformat(retrieved.created_at).tzinfo is not None
    assert datetime.fromisoformat(retrieved.updated_at).tzinfo is not None


def test_get_record_returns_none_for_missing_id(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    initialize_database(db_path)

    assert get_record("not-a-record", db_path) is None


def test_list_records_returns_records_in_deterministic_order(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    initialize_database(db_path)
    first = LandRecord(record_id="record-a", owner_name="Owner A")
    second = LandRecord(record_id="record-b", owner_name="Owner B")
    create_record(second, db_path)
    create_record(first, db_path)

    records = list_records(db_path)

    assert records == [first, second]


def test_parcel_id_and_survey_number_are_not_globally_unique(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    initialize_database(db_path)
    first = LandRecord(parcel_id="shared", survey_number="shared")
    second = LandRecord(parcel_id="shared", survey_number="shared")

    create_record(first, db_path)
    create_record(second, db_path)

    assert len(list_records(db_path)) == 2
