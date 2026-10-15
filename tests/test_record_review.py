import hashlib

from modules.record_review import prepare_reviewed_record


def valid_values():
    return {
        "id": "reviewed-id",
        "parcel_id": "P-100",
        "survey_number": "12/4",
        "owner_name": "Asha Rao",
        "district": "Dharwad",
        "village": "Kumbhar",
        "land_use": "Agricultural",
        "area": "1,200.5",
        "area_unit": "sq m",
        "document_year": "2024",
    }


def test_prepares_reviewed_record_types_and_distinct_scores():
    payload, errors = prepare_reviewed_record(
        valid_values(),
        ocr_confidence=81.25,
        source_document_hash=hashlib.sha256(b"original file").hexdigest(),
    )

    assert errors == {}
    assert payload["id"] == "reviewed-id"
    assert payload["area"] == 1200.5
    assert payload["area_unit"] == "sq m"
    assert payload["document_year"] == 2024
    assert payload["ocr_confidence"] == 81.25
    assert "trust_score" not in payload


def test_reports_required_and_numeric_errors_by_field():
    values = valid_values()
    values.update(parcel_id=" ", area="NaN", document_year="20O4")

    payload, errors = prepare_reviewed_record(
        values,
        ocr_confidence=None,
        source_document_hash=hashlib.sha256(b"original file").hexdigest(),
    )

    assert "parcel_id" in errors
    assert "area" in errors
    assert "document_year" in errors
    assert "parcel_id" not in payload
    assert "area" not in payload
    assert "document_year" not in payload


def test_blank_optional_record_id_uses_database_generated_id_path():
    values = valid_values()
    values["id"] = ""

    payload, errors = prepare_reviewed_record(
        values,
        ocr_confidence=None,
        source_document_hash=hashlib.sha256(b"original file").hexdigest(),
    )

    assert errors == {}
    assert "id" not in payload


def test_rejects_invalid_document_hash():
    payload, errors = prepare_reviewed_record(
        valid_values(), ocr_confidence=None, source_document_hash="not-a-hash"
    )

    assert payload.get("source_document_hash") is None
    assert "source_document_hash" in errors
