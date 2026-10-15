import pytest

from modules.audit import list_events
from modules.database import (
    create_land_record,
    get_land_record,
    initialize_database,
    list_all_alerts,
    list_land_records,
    list_validation_results,
)
from modules.geospatial import evaluate_geospatial_records, load_geojson
from modules.review_workflow import dashboard_snapshot, run_record_validation
from scripts.seed_demo import DEMO_DIR, seed_demo_database


def test_demo_seed_is_idempotent_and_covers_required_outcomes(tmp_path):
    database = tmp_path / "demo.sqlite3"

    first = seed_demo_database(database, create_sample_document=False)
    event_count = len(list_events(database))
    alert_count = len(list_all_alerts(database))
    second = seed_demo_database(database, create_sample_document=False)

    assert first["inserted"] == 10
    assert second["inserted"] == 0
    records = list_land_records(database, limit=1000)
    assert len(records) == 10
    assert len(list_events(database)) == event_count
    assert len(list_all_alerts(database)) == alert_count
    assert any(record["owner_name"] == "Not detected" for record in records)
    assert len([record for record in records if record["survey_number"] == "DEMO-SURVEY-003"]) == 2

    missing_owner = get_land_record(database, "DEMO-0005")
    assert missing_owner is not None
    findings = list_validation_results(database, "DEMO-0005")
    assert any(
        result["rule_code"] == "REQUIRED_OWNER_NAME" and not result["passed"]
        for result in findings
    )
    assert get_land_record(database, "DEMO-0006")["status"] == "flagged"


def test_partial_demo_database_is_not_silently_overwritten(tmp_path):
    database = tmp_path / "partial.sqlite3"
    initialize_database(database)
    create_land_record(
        database,
        {
            "id": "DEMO-0001",
            "parcel_id": "DEMO-PARCEL-001",
            "survey_number": "DEMO-SURVEY-001",
            "owner_name": "Existing local value",
            "district": "Demo District",
            "village": "Maple Example",
            "land_use": "Agricultural",
            "area": 2.5,
            "document_year": 2022,
        },
    )

    with pytest.raises(RuntimeError, match="partially present"):
        seed_demo_database(database, create_sample_document=False)

    assert [record["id"] for record in list_land_records(database)] == ["DEMO-0001"]


def test_demo_validation_and_dashboard_show_full_score_categories(tmp_path):
    database = tmp_path / "demo.sqlite3"
    seed_demo_database(database, create_sample_document=False)
    records = list_land_records(database, limit=1000)
    geojson = load_geojson(DEMO_DIR / "parcel_geometries.geojson")
    spatial = evaluate_geospatial_records(records, geojson)

    assert {
        run_record_validation(
            database,
            record["id"],
            reference_records=_read_references(),
            geospatial_findings=spatial[record["id"]]["findings"],
            geospatial_score=spatial[record["id"]]["score"],
        )["trust_score"]["category"]
        for record in records
    } >= {"High confidence", "Review recommended", "High risk / manual review"}
    alert_categories = {
        alert["category"] for alert in list_all_alerts(database, limit=1000)
    }
    assert {
        "Missing required fields",
        "Low OCR confidence",
        "Duplicate identifiers",
        "Reference-data mismatches",
        "Geospatial anomalies",
    } <= alert_categories

    snapshot = dashboard_snapshot(
        database,
        reference_records=_read_references(),
        geospatial_scores={
            record_id: outcome["score"] for record_id, outcome in spatial.items()
        },
    )
    assert snapshot["complete_score_count"] == 10
    assert snapshot["provisional_score_count"] == 0
    assert all(value > 0 for value in snapshot["trust_score_categories"].values())


def _read_references():
    import csv

    with (DEMO_DIR / "reference_records.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        row["area"] = float(row["area"])
    return rows
