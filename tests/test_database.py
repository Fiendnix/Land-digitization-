import sqlite3
from datetime import datetime

import pytest

from modules.database import (
    create_alert,
    create_audit_event,
    create_land_record,
    create_validation_result,
    delete_land_record,
    get_land_record,
    initialize_database,
    list_alerts,
    list_audit_events,
    list_land_records,
    list_validation_results,
    resolve_alert,
    update_land_record,
)


@pytest.fixture
def database_path(tmp_path):
    path = tmp_path / "test.sqlite3"
    initialize_database(path)
    return path


@pytest.fixture
def record_fields():
    return {
        "parcel_id": "PARCEL-001",
        "survey_number": "SURVEY-12/3",
        "owner_name": "Asha Patel",
        "district": "Test District",
        "village": "Test Village",
        "land_use": "Agricultural",
        "area": 2.5,
        "document_year": 2024,
        "ocr_confidence": 87.5,
    }


def test_initialization_is_repeatable_and_preserves_data(database_path, record_fields):
    created = create_land_record(database_path, record_fields)
    initialize_database(database_path)
    assert get_land_record(database_path, created["id"]) == created


def test_record_insert_and_retrieval_use_stable_id(database_path, record_fields):
    created = create_land_record(database_path, record_fields)
    assert created["id"]
    assert get_land_record(database_path, created["id"]) == created
    assert [row["id"] for row in list_land_records(database_path)] == [created["id"]]
    assert created["status"] == "pending"


def test_record_update_changes_fields_and_updated_timestamp(database_path, record_fields):
    created = create_land_record(database_path, record_fields)
    updated = update_land_record(
        database_path, created["id"], {"owner_name": "Meera Patel", "trust_score": 92}
    )
    assert updated is not None
    assert updated["owner_name"] == "Meera Patel"
    assert updated["trust_score"] == 92
    assert updated["updated_at"] >= created["updated_at"]


def test_record_with_audit_history_cannot_be_deleted(database_path, record_fields):
    record = create_land_record(database_path, record_fields)
    create_validation_result(
        database_path,
        {
            "record_id": record["id"],
            "rule_code": "AREA_CHECK",
            "severity": "low",
            "passed": True,
            "message": "Area is present",
        },
    )
    create_alert(
        database_path,
        {
            "record_id": record["id"],
            "category": "review",
            "severity": "medium",
            "message": "Review the source document",
        },
    )
    assert not delete_land_record(database_path, record["id"])
    assert get_land_record(database_path, record["id"]) is not None
    assert list_validation_results(database_path, record["id"])
    assert list_alerts(database_path, record["id"])


def test_foreign_keys_are_enforced(database_path):
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 0
    with pytest.raises(sqlite3.IntegrityError):
        create_validation_result(
            database_path,
            {
                "record_id": "missing-record",
                "rule_code": "AREA_CHECK",
                "severity": "low",
                "passed": False,
                "message": "No such record",
            },
        )


def test_database_functions_enable_foreign_keys_for_each_connection(database_path):
    # An invalid reference is rejected by the database module's own connection.
    with pytest.raises(sqlite3.IntegrityError):
        create_alert(
            database_path,
            {
                "record_id": "missing-record",
                "category": "review",
                "severity": "high",
                "message": "The parent record is absent",
            },
        )


def test_related_entities_and_canonical_audit_payload(database_path, record_fields):
    record = create_land_record(database_path, record_fields)
    validation = create_validation_result(
        database_path,
        {
            "record_id": record["id"],
            "rule_code": "OWNER_PRESENT",
            "severity": "info",
            "passed": True,
            "message": "Owner name present",
        },
    )
    audit = create_audit_event(
        database_path,
        {
            "record_id": record["id"],
            "action": "record_created",
            "event_timestamp": "2026-01-02T03:04:05+05:30",
            "payload": {"z": 1, "a": "value"},
        },
    )
    alert = create_alert(
        database_path,
        {
            "record_id": record["id"],
            "category": "review",
            "severity": "medium",
            "message": "Manual review required",
        },
    )
    assert validation in list_validation_results(database_path, record["id"])
    assert audit in list_audit_events(database_path, record["id"])
    assert audit["canonical_payload"] == (
        '{"actor":"system","payload":{"a":"value","z":1}}'
    )
    assert datetime.fromisoformat(audit["event_timestamp"]).isoformat() == (
        "2026-01-01T21:34:05+00:00"
    )
    assert alert["resolved"] is False
    resolved = resolve_alert(database_path, alert["id"])
    assert resolved is not None and resolved["resolved"] is True
    assert list_alerts(database_path, record["id"], include_resolved=False) == []


def test_record_validation_rejects_bad_types_and_bounds(database_path, record_fields):
    invalid = {**record_fields, "area": True}
    with pytest.raises(TypeError):
        create_land_record(database_path, invalid)
    invalid = {**record_fields, "ocr_confidence": 101}
    with pytest.raises(ValueError):
        create_land_record(database_path, invalid)
    invalid = {**record_fields, "source_document_hash": "not-a-hash"}
    with pytest.raises(ValueError):
        create_land_record(database_path, invalid)


def test_data_persists_across_module_connections(database_path, record_fields):
    created = create_land_record(database_path, record_fields)
    assert get_land_record(database_path, created["id"])["parcel_id"] == "PARCEL-001"


def test_record_preserves_optional_area_unit_and_source_hash(database_path, record_fields):
    record_fields["area_unit"] = "hectares"
    record_fields["source_document_hash"] = "a" * 64
    created = create_land_record(database_path, record_fields)

    assert created["area_unit"] == "hectares"
    assert created["source_document_hash"] == "a" * 64


def test_initialize_migrates_existing_land_records_table(database_path):
    with sqlite3.connect(database_path) as connection:
        connection.execute("DROP TABLE land_records")
        connection.execute("CREATE TABLE land_records (id TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO land_records (id) VALUES ('existing-record')")

    initialize_database(database_path)

    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(land_records)")}
        assert "area_unit" in columns
        assert connection.execute("SELECT id FROM land_records").fetchone()[0] == "existing-record"


def test_duplicate_record_ids_are_rejected(database_path, record_fields):
    create_land_record(database_path, {"id": "chosen-id", **record_fields})

    with pytest.raises(sqlite3.IntegrityError):
        create_land_record(database_path, {"id": "chosen-id", **record_fields})
