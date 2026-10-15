import math

import pytest

from modules.trust_score import (
    DEFAULT_WEIGHTS,
    calculate_data_consistency,
    calculate_trust_score,
    count_major_inconsistencies,
    validate_weights,
)


def score(**changes):
    inputs = {
        "ocr_confidence": 100,
        "major_inconsistencies": 0,
        "geospatial_score": 100,
        "audit_integrity_score": 100,
    }
    inputs.update(changes)
    return calculate_trust_score(**inputs)


def test_default_weights_sum_to_one_and_formula_is_deterministic():
    validate_weights(DEFAULT_WEIGHTS)
    first = score()
    second = score()

    assert first == second
    assert first["score"] == 100.0
    assert first["is_complete"] is True
    assert first["category"] == "High confidence"
    assert math.isclose(
        sum(component["weighted_contribution"] for component in first["components"].values()),
        first["score"],
        abs_tol=0.05,
    )


@pytest.mark.parametrize(
    ("inputs", "expected"),
    [
        ({"ocr_confidence": 0, "geospatial_score": 0, "audit_integrity_score": 0}, 30.0),
        ({"ocr_confidence": 100, "geospatial_score": 100, "audit_integrity_score": 100,
          "major_inconsistencies": 1}, 92.5),
        ({"ocr_confidence": 0, "geospatial_score": 0, "audit_integrity_score": 0,
          "major_inconsistencies": 4}, 0.0),
    ],
)
def test_boundary_values_and_consistency_deductions(inputs, expected):
    result = score(**inputs)

    assert result["score"] == expected
    assert result["is_complete"] is True
    assert calculate_data_consistency(inputs.get("major_inconsistencies", 0)) == max(
        0.0, 100.0 - 25.0 * inputs.get("major_inconsistencies", 0)
    )


def test_categories_follow_required_boundaries():
    assert score(ocr_confidence=100, geospatial_score=100, audit_integrity_score=100)[
        "category"
    ] == "High confidence"
    assert score(
        ocr_confidence=0,
        geospatial_score=100,
        audit_integrity_score=100,
        major_inconsistencies=0,
    )["category"] == "Review recommended"
    assert score(
        ocr_confidence=0,
        geospatial_score=0,
        audit_integrity_score=0,
    )["category"] == "High risk / manual review"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (79.9, "Review recommended"),
        (80.0, "High confidence"),
        (59.9, "High risk / manual review"),
        (60.0, "Review recommended"),
    ],
)
def test_category_exact_boundaries(value, expected):
    result = calculate_trust_score(
        ocr_confidence=value,
        major_inconsistencies=0,
        geospatial_score=0,
        audit_integrity_score=0,
        weights={"O": 1.0, "D": 0.0, "G": 0.0, "H": 0.0},
    )

    assert result["score"] == value
    assert result["category"] == expected


def test_missing_components_are_provisional_and_weights_are_not_redistributed():
    result = calculate_trust_score(
        ocr_confidence=80,
        major_inconsistencies=1,
        geospatial_score=None,
        audit_integrity_score=None,
    )

    assert result["status"] == "provisional"
    assert result["is_complete"] is False
    assert result["score"] == 46.5
    assert result["category"] == "Provisional (incomplete)"
    assert result["missing_components"] == ["G", "H"]
    assert result["components"]["G"]["input_score"] is None
    assert result["components"]["H"]["input_score"] is None
    assert result["components"]["G"]["weighted_contribution"] == 0
    assert result["components"]["H"]["weighted_contribution"] == 0
    assert "not redistributed" in result["explanation"]


def test_missing_ocr_and_audit_are_not_assigned_perfect_scores():
    result = calculate_trust_score(
        ocr_confidence=None,
        major_inconsistencies=0,
        geospatial_score=75,
        audit_integrity_score=None,
    )

    assert result["components"]["O"]["input_score"] is None
    assert result["components"]["H"]["input_score"] is None
    assert result["components"]["O"]["weighted_contribution"] == 0
    assert result["components"]["H"]["weighted_contribution"] == 0
    assert result["score"] == 48.8


@pytest.mark.parametrize(
    "weights",
    [
        {"O": 0.3, "D": 0.3, "G": 0.25, "H": 0.1},
        {"O": 0.3, "D": 0.3, "G": 0.25, "H": 0.15, "X": 0},
        {"O": -0.1, "D": 0.3, "G": 0.4, "H": 0.4},
    ],
)
def test_invalid_weights_are_rejected(weights):
    with pytest.raises(ValueError):
        validate_weights(weights)


def test_component_input_range_and_inconsistency_count_are_validated():
    with pytest.raises(ValueError):
        score(ocr_confidence=101)
    with pytest.raises(ValueError):
        score(major_inconsistencies=-1)
    with pytest.raises(TypeError):
        score(major_inconsistencies=True)


def test_major_inconsistencies_count_distinct_critical_issues_once():
    findings = [
        {"rule_code": "REQUIRED_AREA", "severity": "critical", "passed": False},
        {"rule_code": "AREA_POSITIVE", "severity": "critical", "passed": False},
        {"rule_code": "REQUIRED_LAND_USE", "severity": "critical", "passed": False},
        {"rule_code": "LAND_USE_ALLOWED", "severity": "critical", "passed": False},
        {"rule_code": "DUPLICATE_PARCEL_ID", "severity": "medium", "passed": False},
        {"rule_code": "DOCUMENT_YEAR_RANGE", "severity": "critical", "passed": True},
    ]

    assert count_major_inconsistencies(findings) == 2


def test_breakdown_contributions_reconcile_with_displayed_total():
    result = score(
        ocr_confidence=83.27,
        major_inconsistencies=2,
        geospatial_score=76.44,
        audit_integrity_score=61.19,
    )
    contribution_total = sum(
        component["weighted_contribution"] for component in result["components"].values()
    )

    assert round(contribution_total, 1) == result["score"]
    assert result["components"]["D"]["input_score"] == 50.0
    assert result["data_consistency_calculation"] == "D = max(0, 100 - 25 × 2) = 50.0"
