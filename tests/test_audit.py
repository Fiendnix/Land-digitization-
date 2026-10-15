import json
import sqlite3

import pytest

from modules.audit import GENESIS_HASH, append_event, list_events, verify_chain
from modules.database import (
    create_land_record,
    initialize_database,
    persist_validation_run,
    set_human_reviewed,
    update_land_record,
)
from modules.validation import validate_record


def new_database(tmp_path):
    path = tmp_path / "audit.sqlite3"
    initialize_database(path)
    record = create_land_record(
        path,
        {
            "id": "record-audit",
            "parcel_id": "P-AUDIT",
            "survey_number": "S-1",
            "owner_name": "Test Owner",
            "district": "Test District",
            "village": "Test Village",
            "land_use": "Agricultural",
            "area": 1.5,
            "document_year": 2024,
        },
    )
    return path, record


def add_event(path, record_id="record-audit", **kwargs):
    return append_event(
        path,
        record_id=record_id,
        action="test_action",
        actor="pytest",
        payload={"message": "land record event", "index": 1},
        **kwargs,
    )


def alter_event(path, position, assignments):
    columns = ", ".join(f"{column} = ?" for column in assignments)
    with sqlite3.connect(path) as connection:
        connection.execute(
            f"UPDATE audit_events SET {columns} WHERE chain_position = ?",
            (*assignments.values(), position),
        )


def test_genesis_event_uses_fixed_genesis_hash_and_validates(tmp_path):
    path, record = new_database(tmp_path)
    events = list_events(path)

    assert len(events) == 1
    assert events[0]["record_id"] == record["id"]
    assert events[0]["action"] == "record_created"
    assert events[0]["previous_hash"] == GENESIS_HASH
    assert verify_chain(path)["valid"] is True


def test_multiple_global_events_link_and_verify(tmp_path):
    path, record = new_database(tmp_path)
    second = add_event(path, record["id"])
    third = append_event(
        path,
        record_id=record["id"],
        action="validation_run",
        actor="validation_engine",
        payload={"finding_count": 4},
    )
    events = list_events(path)

    assert [event["chain_position"] for event in events] == [1, 2, 3]
    assert second["previous_hash"] == events[0]["event_hash"]
    assert third["previous_hash"] == second["event_hash"]
    assert verify_chain(path)["valid"] is True


def test_operation_key_prevents_duplicate_rerun_event(tmp_path):
    path, record = new_database(tmp_path)
    first = add_event(path, record["id"], operation_key="rerun-key-1")
    second = add_event(path, record["id"], operation_key="rerun-key-1")

    assert first["event_id"] == second["event_id"]
    assert len(list_events(path)) == 2
    assert verify_chain(path)["valid"] is True


def test_reusing_operation_key_for_different_event_is_rejected(tmp_path):
    path, record = new_database(tmp_path)
    add_event(path, record["id"], operation_key="rerun-key-2")

    try:
        append_event(
            path,
            record_id=record["id"],
            action="different_action",
            actor="pytest",
            payload={"different": True},
            operation_key="rerun-key-2",
        )
    except ValueError as error:
        assert "different audit event" in str(error)
    else:
        raise AssertionError("A reused operation key with different data must be rejected")

    assert len(list_events(path)) == 2
    assert verify_chain(path)["valid"] is True


def test_record_edits_and_status_changes_are_audited(tmp_path):
    path, record = new_database(tmp_path)
    edited = update_land_record(path, record["id"], {"owner_name": "Reviewed Owner"})
    findings = validate_record(edited, existing_records=[edited])
    persist_validation_run(path, record["id"], "review-run-1", findings, [])
    set_human_reviewed(path, record["id"])
    events = list_events(path)

    assert [event["action"] for event in events] == [
        "record_created",
        "record_edited",
        "validation_run",
        "human_review_completed",
        "status_changed",
    ]
    assert events[-1]["payload"]["old_status"] == "pending"
    assert events[-1]["payload"]["new_status"] == "validated"
    assert verify_chain(path)["valid"] is True


def test_record_status_cannot_be_changed_directly(tmp_path):
    path, record = new_database(tmp_path)

    with pytest.raises(ValueError, match="managed by the review workflow"):
        update_land_record(path, record["id"], {"status": "validated"})


def test_record_write_rolls_back_when_existing_chain_is_invalid(tmp_path):
    path, _ = new_database(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE audit_events SET event_hash = ? WHERE chain_position = 1",
            ("c" * 64,),
        )

    try:
        create_land_record(
            path,
            {
                "id": "must-roll-back",
                "parcel_id": "P-ROLLBACK",
                "survey_number": "S-ROLLBACK",
                "owner_name": "Test",
                "district": "Test",
                "village": "Test",
                "land_use": "Agricultural",
                "area": 1,
                "document_year": 2024,
            },
        )
    except ValueError as error:
        assert "invalid audit chain" in str(error)
    else:
        raise AssertionError("A record must not be written without its valid audit event")

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT 1 FROM land_records WHERE id = 'must-roll-back'"
        ).fetchone() is None
    assert len(list_events(path)) == 1


def test_modified_historical_payload_is_detected(tmp_path):
    path, record = new_database(tmp_path)
    add_event(path, record["id"])
    alter_event(
        path,
        1,
        {"canonical_payload": json.dumps(
            {"actor": "system", "payload": {"tampered": True}},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )},
    )

    result = verify_chain(path)
    assert result["valid"] is False
    assert result["first_invalid_event"]["chain_position"] == 1
    assert "event_hash" in result["reason"]


def test_modified_previous_hash_is_detected(tmp_path):
    path, record = new_database(tmp_path)
    add_event(path, record["id"])
    alter_event(path, 2, {"previous_hash": "a" * 64})

    result = verify_chain(path)
    assert result["valid"] is False
    assert result["first_invalid_event"]["chain_position"] == 2
    assert "previous_hash" in result["reason"]


def test_modified_event_hash_is_detected(tmp_path):
    path, _ = new_database(tmp_path)
    alter_event(path, 1, {"event_hash": "b" * 64})

    result = verify_chain(path)
    assert result["valid"] is False
    assert result["first_invalid_event"]["chain_position"] == 1
    assert "event_hash" in result["reason"]


def test_missing_middle_event_is_detected(tmp_path):
    path, record = new_database(tmp_path)
    add_event(path, record["id"])
    append_event(path, record_id=record["id"], action="third", payload={"n": 3})
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM audit_events WHERE chain_position = 2")

    result = verify_chain(path)
    assert result["valid"] is False
    assert result["first_invalid_event"]["chain_position"] == 3
    assert "missing or" in result["reason"]


def test_deleted_tail_is_detected_by_persisted_chain_head(tmp_path):
    path, record = new_database(tmp_path)
    add_event(path, record["id"])
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM audit_events WHERE chain_position = 2")

    result = verify_chain(path)
    assert result["valid"] is False
    assert result["first_invalid_event"]["chain_position"] == 1
    assert "head state" in result["reason"]


def test_reordered_events_are_detected(tmp_path):
    path, record = new_database(tmp_path)
    add_event(path, record["id"])
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE audit_events SET chain_position = 99 WHERE chain_position = 1")
        connection.execute("UPDATE audit_events SET chain_position = 1 WHERE chain_position = 2")
        connection.execute("UPDATE audit_events SET chain_position = 2 WHERE chain_position = 99")

    result = verify_chain(path)
    assert result["valid"] is False
    assert result["first_invalid_event"]["chain_position"] == 1
