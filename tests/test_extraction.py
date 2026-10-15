from modules.extraction import extract_fields


def test_extracts_labeled_fields_without_consuming_neighboring_lines():
    result = extract_fields(
        """Parcel ID: P-1209
Survey Number: 18/2A
Owner Name: Asha Devi
District: Dharwad
Village: Kumbhar
Land Use: Agricultural
Area: 2,500.75 sq m
Date: 13/04/2024"""
    )

    assert result.parcel_id == "P-1209"
    assert result.survey_number == "18/2A"
    assert result.owner_name == "Asha Devi"
    assert result.district == "Dharwad"
    assert result.village == "Kumbhar"
    assert result.land_use == "Agricultural"
    assert result.area == 2500.75
    assert result.area_unit == "sq m"
    assert result.document_year == 2024
    assert all(0 <= score <= 100 for score in result.field_confidences.values())


def test_supports_alternate_land_record_labels_and_following_line_values():
    result = extract_fields(
        """Plot No
PL-22
Khasra No: 7/4
Name of the Owner: N. Rao
Tehsil: Mysuru
Mouza: Belavadi
Total Area: 5.25 hectares
Document Year: 2020"""
    )

    assert result.parcel_id == "PL-22"
    assert result.survey_number == "7/4"
    assert result.owner_name == "N. Rao"
    assert result.district == "Mysuru"
    assert result.village == "Belavadi"
    assert result.area == 5.25
    assert result.area_unit == "hectares"
    assert result.document_year == 2020


def test_missing_values_remain_none_and_have_no_confidence():
    result = extract_fields("Record copy\nAgricultural")

    assert result.parcel_id is None
    assert result.survey_number is None
    assert result.owner_name is None
    assert result.district is None
    assert result.village is None
    assert result.land_use == "Agricultural"
    assert result.area is None
    assert result.document_year is None
    assert "parcel_id" not in result.field_confidences


def test_ignores_unlabeled_numbers_and_invalid_years():
    result = extract_fields("Reference 12345\nDate: 04/02/1700\nArea: unknown")

    assert result.parcel_id is None
    assert result.area is None
    assert result.document_year is None


def test_adjacent_inline_labels_are_split():
    result = extract_fields("Owner Name: Meera Rao District: Mandya Village: Somanahalli")

    assert result.owner_name == "Meera Rao"
    assert result.district == "Mandya"
    assert result.village == "Somanahalli"
