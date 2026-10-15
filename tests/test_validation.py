from modules.validation import ValidationConfig, validate_record, validation_result_fields
from modules.database import (
    create_land_record,
    create_validation_result,
    initialize_database,
)


def valid_record(**changes):
    return {
        "id": "record-1",
        "parcel_id": "PARCEL-001",
        "survey_number": "SURVEY-12/3",
        "owner_name": "Asha Patel",
        "district": "Test District",
        "village": "Test Village",
        "land_use": "Agricultural",
        "area": 2.5,
        "document_year": 2024,
        "ocr_confidence": 87.5,
        **changes,
    }


def passed_reference():
    return [
        {
            "id": "reference-1",
            "parcel_id": "PARCEL-001",
            "survey_number": "SURVEY-12/3",
            "owner_name": "asha   patel",
            "land_use": "agricultural",
            "area": 2.5,
        }
    ]


def find(results, rule_code):
    return next(result for result in results if result["rule_code"] == rule_code)


def test_valid_record_passes_with_reference_and_confidence():
    record = valid_record()
    results = validate_record(
        record,
        existing_records=[record],
        reference_records=passed_reference(),
        field_confidences={field: 85 for field in (
            "survey_number", "owner_name", "district", "village", "land_use", "area"
        )},
    )

    assert all(result["passed"] for result in results)
    assert find(results, "REFERENCE_OWNER_NAME")["passed"] is True
    assert find(results, "OCR_CONFIDENCE")["passed"] is True
    assert find(results, "DUPLICATE_PARCEL_ID")["passed"] is True


def test_missing_fields_produce_all_applicable_failures():
    record = valid_record(
        survey_number=" \t",
        owner_name="",
        district=None,
        village="   ",
        land_use="",
        area=None,
    )
    results = validate_record(record)

    for field in ("survey_number", "owner_name", "district", "village", "land_use", "area"):
        result = find(results, f"REQUIRED_{field.upper()}")
        assert result["passed"] is False
        assert result["message"]
    assert find(results, "AREA_POSITIVE")["passed"] is False
    assert len(results) > 1


def test_invalid_area_and_year_are_reported():
    results = validate_record(valid_record(area="1,2", document_year="1700"))

    assert find(results, "AREA_POSITIVE")["passed"] is False
    assert find(results, "DOCUMENT_YEAR_RANGE")["passed"] is False


def test_year_and_land_use_rules_can_be_configured():
    config = ValidationConfig(
        minimum_document_year=1900,
        maximum_document_year=2025,
        allowed_land_uses=("Agriculture", "Residential"),
        unknown_land_use_values=("not recorded",),
    )
    results = validate_record(
        valid_record(document_year=2026, land_use="not recorded"),
        config=config,
    )

    assert find(results, "DOCUMENT_YEAR_RANGE")["passed"] is False
    assert find(results, "LAND_USE_UNKNOWN")["passed"] is True
    assert find(results, "LAND_USE_UNKNOWN")["severity"] == "medium"


def test_duplicate_parcel_and_survey_are_reported_as_warnings():
    current = valid_record()
    other = valid_record(
        id="record-2",
        parcel_id=" parcel-001 ",
        survey_number="survey-12/3",
    )
    results = validate_record(current, existing_records=[current, other])

    parcel = find(results, "DUPLICATE_PARCEL_ID")
    survey = find(results, "DUPLICATE_SURVEY_NUMBER")
    assert parcel["passed"] is False and parcel["severity"] == "medium"
    assert survey["passed"] is False and survey["severity"] == "medium"
    assert parcel["details"]["matching_record_ids"] == ["record-2"]
    assert "fraud" not in parcel["message"].casefold()


def test_reference_mismatches_are_reported_for_each_compared_field():
    reference = passed_reference()[0]
    reference.update(owner_name="Different Owner", area=3.0, survey_number="OTHER-9", land_use="Forest")
    results = validate_record(valid_record(), reference_records=[reference])

    for code in (
        "REFERENCE_OWNER_NAME",
        "REFERENCE_AREA",
        "REFERENCE_SURVEY_NUMBER",
        "REFERENCE_LAND_USE",
    ):
        assert find(results, code)["passed"] is False
        assert find(results, code)["message"]


def test_exact_parcel_reference_takes_precedence_over_duplicate_survey_match():
    exact = passed_reference()[0]
    other = {
        **exact,
        "id": "reference-duplicate-survey",
        "parcel_id": "PARCEL-OTHER",
        "owner_name": "Unrelated Fictional Owner",
        "area": 99.0,
    }

    results = validate_record(
        valid_record(),
        reference_records=[exact, other],
    )

    assert find(results, "REFERENCE_OWNER_NAME")["passed"] is True
    assert find(results, "REFERENCE_AREA")["passed"] is True


def test_unavailable_or_empty_reference_data_is_not_a_match():
    unavailable = validate_record(valid_record(), reference_records=None)
    empty = validate_record(valid_record(), reference_records=[])

    assert find(unavailable, "REFERENCE_DATA_UNAVAILABLE")["passed"] is False
    assert "not been reference-verified" in find(
        unavailable, "REFERENCE_DATA_UNAVAILABLE"
    )["message"]
    assert find(empty, "REFERENCE_DATA_EMPTY")["passed"] is False
    assert not any(result["rule_code"] == "REFERENCE_MATCH" for result in unavailable + empty)


def test_reference_self_comparison_is_skipped():
    current = valid_record()
    results = validate_record(current, reference_records=[current])

    assert find(results, "REFERENCE_SELF_MATCH_SKIPPED")["passed"] is False
    assert not any(result["rule_code"] == "REFERENCE_OWNER_NAME" for result in results)


def test_low_and_missing_ocr_confidences_are_never_treated_as_high():
    low = validate_record(valid_record(ocr_confidence=40))
    missing = validate_record(valid_record(ocr_confidence=None))

    assert find(low, "OCR_CONFIDENCE")["passed"] is False
    assert find(missing, "OCR_CONFIDENCE_UNAVAILABLE")["passed"] is False


def test_unconfident_or_unavailable_field_scores_are_flagged():
    results = validate_record(
        valid_record(),
        field_confidences={"owner_name": 20, "survey_number": 90},
    )

    assert find(results, "FIELD_CONFIDENCE_OWNER_NAME")["passed"] is False
    assert find(results, "FIELD_CONFIDENCE_AREA")["passed"] is False
    assert "not confidently extracted" in find(
        results, "FIELD_CONFIDENCE_AREA"
    )["message"]


def test_result_can_be_converted_to_existing_database_shape():
    result = find(
        validate_record(valid_record(), reference_records=None),
        "REFERENCE_DATA_UNAVAILABLE",
    )
    fields = validation_result_fields("record-1", result)

    assert fields == {
        "record_id": "record-1",
        "rule_code": "REFERENCE_DATA_UNAVAILABLE",
        "severity": "info",
        "passed": False,
        "message": result["message"],
    }
    assert "details" not in fields


def test_validation_result_is_accepted_by_database(tmp_path):
    database_path = tmp_path / "validation.sqlite3"
    initialize_database(database_path)
    record = create_land_record(database_path, valid_record())
    result = find(
        validate_record(record, reference_records=None),
        "REFERENCE_DATA_UNAVAILABLE",
    )

    stored = create_validation_result(
        database_path,
        validation_result_fields(record["id"], result),
    )

    assert stored["record_id"] == record["id"]
    assert stored["rule_code"] == "REFERENCE_DATA_UNAVAILABLE"
    assert stored["severity"] == "info"
    assert stored["passed"] is False
