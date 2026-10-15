from __future__ import annotations

import pytest

from modules.audit import list_events
from modules.database import (
    create_alert,
    create_land_record,
    get_land_record,
    initialize_database,
    list_all_alerts,
    persist_validation_run,
    resolve_alert,
    update_land_record,
)
from modules.review_workflow import (
    complete_human_review,
    dashboard_snapshot,
    run_record_validation,
)


@pytest.fixture
def database_path(tmp_path):
    path = tmp_path / "review-workflow.sqlite3"
    initialize_database(path)
    return path


def make_record(database_path, **changes):
    fields = {
        "parcel_id": "PARCEL-001",
        "survey_number": "SURVEY-001",
        "owner_name": "Asha Patel",
        "district": "North District",
        "village": "Example Village",
        "land_use": "Agricultural",
        "area": 2.5,
        "document_year": 2024,
        "ocr_confidence": 88.0,
    }
    fields.update(changes)
    return create_land_record(database_path, fields)


def failed_blocking_run(database_path, record_id, run_id="failed-run"):
    finding = {
        "rule_code": "REQUIRED_TEST_BLOCKER",
        "severity": "critical",
        "passed": False,
        "message": "A blocking review issue is present.",
    }
    return persist_validation_run(
        database_path,
        record_id,
        run_id,
        [finding],
        [
            {
                "category": "Missing required fields",
                "severity": "critical",
                "message": finding["message"],
                "suggested_action": "Correct the issue and rerun validation.",
                "issue_key": f"{record_id}:REQUIRED_TEST_BLOCKER",
            }
        ],
    )


def test_validation_generates_and_deduplicates_alerts(database_path):
    make_record(database_path)
    second = make_record(
        database_path, parcel_id="PARCEL-002", owner_name="Meera Patel"
    )

    first_run = run_record_validation(database_path, second["id"])
    second_run = run_record_validation(database_path, second["id"])

    duplicate_alerts = [
        alert
        for alert in list_all_alerts(database_path)
        if alert["record_id"] == second["id"]
        and alert["category"] == "Duplicate identifiers"
    ]
    assert first_run["audit_logged"] is True
    assert second_run["audit_logged"] is True
    assert len(duplicate_alerts) == 1
    assert duplicate_alerts[0]["severity"] == "medium"
    assert "Compare the records" in duplicate_alerts[0]["suggested_action"]
    assert get_land_record(database_path, second["id"])["status"] == "pending"


def test_reference_mismatch_and_low_ocr_create_actionable_alerts(database_path):
    record = make_record(
        database_path,
        owner_name="Corrected Name",
        ocr_confidence=20.0,
    )
    reference = [
        {
            "id": "authoritative-sample",
            "parcel_id": record["parcel_id"],
            "survey_number": record["survey_number"],
            "owner_name": "Different Name",
            "area": record["area"],
            "land_use": record["land_use"],
        }
    ]

    result = run_record_validation(
        database_path,
        record["id"],
        reference_records=reference,
    )

    categories = {alert["category"] for alert in result["alerts"]}
    assert "Reference-data mismatches" in categories
    assert "Low OCR confidence" in categories
    assert all(alert["suggested_action"] for alert in result["alerts"])
    assert result["trust_score"]["is_complete"] is False


def test_alert_resolution_preserves_history_and_is_audited(database_path):
    record = make_record(database_path)
    other = make_record(
        database_path, parcel_id="PARCEL-002", survey_number=record["survey_number"]
    )
    run_record_validation(database_path, other["id"])
    alert = next(
        alert
        for alert in list_all_alerts(database_path)
        if alert["record_id"] == other["id"]
    )

    resolved = resolve_alert(database_path, alert["id"])

    assert resolved is not None and resolved["resolved"] is True
    historical = [
        item for item in list_all_alerts(database_path) if item["id"] == alert["id"]
    ]
    assert len(historical) == 1 and historical[0]["resolved"] is True
    assert any(
        event["action"] == "alert_resolved"
        and event["payload"]["alert_id"] == alert["id"]
        for event in list_events(database_path)
    )


def test_critical_alert_cannot_leave_record_unflagged(database_path):
    record = make_record(database_path)

    alert = create_alert(
        database_path,
        {
            "record_id": record["id"],
            "category": "Missing required fields",
            "severity": "critical",
            "message": "A blocking issue needs review.",
        },
    )

    assert alert["resolved"] is False
    assert get_land_record(database_path, record["id"])["status"] == "flagged"
    resolve_alert(database_path, alert["id"])
    assert get_land_record(database_path, record["id"])["status"] == "pending"


def test_status_transitions_require_validation_and_explicit_review(database_path):
    record = make_record(database_path)
    assert get_land_record(database_path, record["id"])["status"] == "pending"
    with pytest.raises(ValueError, match="Run validation"):
        complete_human_review(database_path, record["id"])

    run_record_validation(database_path, record["id"])
    verified = complete_human_review(database_path, record["id"])
    assert verified["status"] == "validated"
    assert verified["human_reviewed"] == 1

    updated = update_land_record(
        database_path, record["id"], {"owner_name": "Reviewed Again"}
    )
    assert updated["status"] == "pending"
    assert updated["human_reviewed"] == 0
    with pytest.raises(ValueError, match="Re-run validation"):
        complete_human_review(database_path, record["id"])

    run_record_validation(database_path, record["id"])
    assert complete_human_review(database_path, record["id"])["status"] == "validated"


def test_blocking_finding_flags_record_until_revalidated_and_reviewed(database_path):
    record = make_record(database_path)
    outcome = failed_blocking_run(database_path, record["id"])

    assert outcome["status"] == "flagged"
    assert get_land_record(database_path, record["id"])["status"] == "flagged"
    run_record_validation(database_path, record["id"])
    assert get_land_record(database_path, record["id"])["status"] == "flagged"
    with pytest.raises(ValueError, match="open alert"):
        complete_human_review(database_path, record["id"])

    blocking_alert = outcome["alerts"][0]
    resolve_alert(database_path, blocking_alert["id"])
    assert get_land_record(database_path, record["id"])["status"] == "pending"

    result = run_record_validation(database_path, record["id"])
    assert result["status"] == "pending"
    assert complete_human_review(database_path, record["id"])["status"] == "validated"


def test_dashboard_aggregates_statuses_alerts_activity_and_provisional_scores(
    database_path,
):
    verified = make_record(database_path)
    flagged = make_record(
        database_path, parcel_id="PARCEL-002", survey_number="SURVEY-002"
    )
    make_record(
        database_path, parcel_id="PARCEL-003", survey_number="SURVEY-003"
    )
    run_record_validation(database_path, verified["id"])
    complete_human_review(database_path, verified["id"])
    failure = failed_blocking_run(database_path, flagged["id"], "dashboard-failure")

    snapshot = dashboard_snapshot(database_path)

    assert snapshot["total"] == 3
    assert snapshot["verified"] == 1
    assert snapshot["flagged"] == 1
    assert snapshot["pending_review"] == 1
    assert snapshot["average_complete_trust_score"] is None
    assert snapshot["provisional_score_count"] == 3
    assert snapshot["average_provisional_subtotal"] is not None
    assert any(
        alert["id"] == failure["alerts"][0]["id"]
        for alert in snapshot["recent_alerts"]
    )
    assert snapshot["recent_activity"]


def test_reference_unavailable_does_not_generate_mismatch_alert(database_path):
    record = make_record(database_path)

    result = run_record_validation(database_path, record["id"])

    assert any(
        finding["rule_code"] == "REFERENCE_DATA_UNAVAILABLE"
        and finding["passed"] is False
        for finding in result["findings"]
    )
    assert not any(
        alert["category"] == "Reference-data mismatches"
        for alert in result["alerts"]
    )


def test_unavailable_geometry_is_not_mislabeled_as_a_geospatial_anomaly(database_path):
    record = make_record(database_path)
    result = run_record_validation(
        database_path,
        record["id"],
        geospatial_findings=[
            {
                "rule_code": "GEOSPATIAL_DATA_UNAVAILABLE",
                "severity": "info",
                "passed": False,
                "message": "No geometry is available for this record.",
            }
        ],
        geospatial_score=None,
    )

    assert not any(
        alert["category"] == "Geospatial anomalies"
        for alert in result["alerts"]
    )
    assert "G" in result["trust_score"]["missing_components"]


@pytest.mark.parametrize(
    ("confidence", "expects_low_ocr_alert"),
    [(None, False), (95.0, False), (10.0, True)],
)
def test_low_ocr_alert_requires_an_actual_low_confidence_finding(
    database_path, confidence, expects_low_ocr_alert
):
    record = make_record(
        database_path,
        parcel_id=f"PARCEL-{confidence}",
        survey_number=f"SURVEY-{confidence}",
        ocr_confidence=confidence,
    )

    result = run_record_validation(database_path, record["id"])

    assert any(
        finding["rule_code"] == "FIELD_CONFIDENCE_UNAVAILABLE"
        for finding in result["findings"]
    )
    assert any(
        alert["category"] == "Low OCR confidence"
        for alert in result["alerts"]
    ) is expects_low_ocr_alert
