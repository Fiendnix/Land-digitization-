"""Deterministic validation rules for reviewed land records.

Validation is independent of SQLite and Streamlit. The result severity
``medium`` represents a warning because it is one of the severities accepted
by the existing ``validation_results`` schema.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, NotRequired, TypedDict

Record = Mapping[str, Any]


class ValidationResult(TypedDict):
    """A validation result, with optional diagnostic details."""

    rule_code: str
    severity: str
    passed: bool
    message: str
    details: NotRequired[dict[str, Any]]


_DEFAULT_LAND_USES = (
    "Agricultural",
    "Residential",
    "Commercial",
    "Industrial",
    "Forest",
    "Barren",
    "Wasteland",
    "Wetland",
    "Grazing",
    "Plantation",
    "Orchard",
    "Garden",
    "Institutional",
    "Recreational",
    "Mixed",
)
_DEFAULT_UNKNOWN_LAND_USES = ("unknown", "unspecified", "not known", "n/a")
_REQUIRED_FIELDS = (
    ("survey_number", "Survey number"),
    ("owner_name", "Owner name"),
    ("district", "District"),
    ("village", "Village"),
    ("land_use", "Land use"),
    ("area", "Area"),
)
_NUMERIC_TEXT = re.compile(
    r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+|\d{1,3}(?:,\d{3})+(?:\.\d*)?)$"
)
_MISSING_TEXT_MARKERS = {
    "not detected",
    "not available",
    "unavailable",
    "n/a",
    "na",
    "none",
    "null",
    "unknown",
    "unknown (not detected)",
}


@dataclass(frozen=True)
class ValidationConfig:
    """Configurable value and OCR thresholds for deterministic checks."""

    minimum_document_year: int = 1800
    maximum_document_year: int = 2200
    allowed_land_uses: tuple[str, ...] = _DEFAULT_LAND_USES
    unknown_land_use_values: tuple[str, ...] = _DEFAULT_UNKNOWN_LAND_USES
    low_ocr_confidence_threshold: float = 60.0
    low_field_confidence_threshold: float = 60.0
    reference_area_tolerance: float = 0.01

    def __post_init__(self) -> None:
        if self.minimum_document_year > self.maximum_document_year:
            raise ValueError("minimum_document_year must not exceed maximum_document_year")
        for name, value in (
            ("low_ocr_confidence_threshold", self.low_ocr_confidence_threshold),
            ("low_field_confidence_threshold", self.low_field_confidence_threshold),
        ):
            if not math.isfinite(value) or not 0 <= value <= 100:
                raise ValueError(f"{name} must be between 0 and 100")
        if not math.isfinite(self.reference_area_tolerance) or self.reference_area_tolerance < 0:
            raise ValueError("reference_area_tolerance must be finite and non-negative")
        if not self.allowed_land_uses:
            raise ValueError("allowed_land_uses must contain at least one value")
        if not self.unknown_land_use_values:
            raise ValueError("unknown_land_use_values must contain at least one value")


def _normalize_text(value: Any) -> str:
    """Normalize whitespace and Unicode for case-insensitive comparisons."""
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.split()).casefold()


def _parse_area(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not _NUMERIC_TEXT.fullmatch(text):
            return None
        try:
            number = float(text.replace(",", ""))
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _parse_year(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"\d{1,4}", text):
            return int(text)
    return None


def _has_field_value(record: Record, field: str) -> bool:
    if field == "area":
        return _parse_area(record.get(field)) is not None
    normalized = _normalize_text(record.get(field))
    if not normalized:
        return False
    if field == "land_use":
        return True
    return normalized not in _MISSING_TEXT_MARKERS


def _result(
    rule_code: str,
    severity: str,
    passed: bool,
    message: str,
    details: dict[str, Any] | None = None,
) -> ValidationResult:
    result: ValidationResult = {
        "rule_code": rule_code,
        "severity": severity,
        "passed": passed,
        "message": message,
    }
    if details is not None:
        result["details"] = details
    return result


def _duplicate_checks(
    record: Record,
    existing_records: Iterable[Record],
) -> list[ValidationResult]:
    findings: list[ValidationResult] = []
    current_id = record.get("id")
    for field, label, rule_code in (
        ("parcel_id", "Parcel ID", "DUPLICATE_PARCEL_ID"),
        ("survey_number", "Survey number", "DUPLICATE_SURVEY_NUMBER"),
    ):
        value = _normalize_text(record.get(field))
        if not value:
            continue
        duplicate_ids: list[str] = []
        for other in existing_records:
            if other is record or (current_id is not None and other.get("id") == current_id):
                continue
            if _normalize_text(other.get(field)) == value:
                other_id = other.get("id")
                duplicate_ids.append(str(other_id) if other_id is not None else "unknown")
        is_duplicate = bool(duplicate_ids)
        warning = "Possible duplicate found; review the records before drawing conclusions."
        findings.append(
            _result(
                rule_code,
                "medium" if is_duplicate else "info",
                not is_duplicate,
                f"{label} {'matches an existing record' if is_duplicate else 'is unique among the supplied records'}. "
                + (warning if is_duplicate else ""),
                {"matching_record_ids": duplicate_ids} if is_duplicate else None,
            )
        )
    return findings


def _reference_checks(
    record: Record,
    reference_records: Iterable[Record] | None,
    config: ValidationConfig,
) -> list[ValidationResult]:
    if reference_records is None:
        return [
            _result(
                "REFERENCE_DATA_UNAVAILABLE",
                "info",
                False,
                "Reference data is unavailable; this record has not been reference-verified.",
            )
        ]

    reference_rows = list(reference_records)
    if not reference_rows:
        return [
            _result(
                "REFERENCE_DATA_EMPTY",
                "info",
                False,
                "Reference data is available but contains no records; this record has not been reference-verified.",
            )
        ]

    parcel_id = _normalize_text(record.get("parcel_id"))
    survey_number = _normalize_text(record.get("survey_number"))
    parcel_matches = [
        row
        for row in reference_rows
        if parcel_id and _normalize_text(row.get("parcel_id")) == parcel_id
    ]
    matches = parcel_matches or [
        row
        for row in reference_rows
        if survey_number and _normalize_text(row.get("survey_number")) == survey_number
    ]
    if not matches:
        return [
            _result(
                "REFERENCE_RECORD_NOT_FOUND",
                "medium",
                False,
                "No corresponding record was found in the supplied reference dataset; no reference match is established.",
            )
        ]

    # An ID match identifies a corresponding reference row. Do not compare a
    # record with itself if the caller passed a shared collection.
    record_id = record.get("id")
    matches = [
        row
        for row in matches
        if row is not record and (record_id is None or row.get("id") != record_id)
    ]
    if not matches:
        return [
            _result(
                "REFERENCE_SELF_MATCH_SKIPPED",
                "info",
                False,
                "The matching reference entry has the same record ID; self-comparison was skipped.",
            )
        ]

    comparison_fields = (
        ("owner_name", "Owner name"),
        ("survey_number", "Survey number"),
        ("land_use", "Land use"),
    )
    findings: list[ValidationResult] = []
    for field, label in comparison_fields:
        candidate = _normalize_text(record.get(field))
        reference_values = {_normalize_text(row.get(field)) for row in matches}
        reference_values.discard("")
        if not candidate or not reference_values:
            continue
        mismatch = candidate not in reference_values
        findings.append(
            _result(
                f"REFERENCE_{field.upper()}",
                "medium" if mismatch else "info",
                not mismatch,
                (
                    f"{label} does not match the corresponding reference record."
                    if mismatch
                    else f"{label} matches the corresponding reference record."
                ),
                {"record_value": record.get(field), "reference_values": sorted(reference_values)}
                if mismatch
                else None,
            )
        )

    candidate_area = _parse_area(record.get("area"))
    reference_areas = {
        area
        for row in matches
        if (area := _parse_area(row.get("area"))) is not None
    }
    if candidate_area is not None and reference_areas:
        mismatch = not any(
            math.isclose(
                candidate_area,
                reference_area,
                rel_tol=0.0,
                abs_tol=config.reference_area_tolerance,
            )
            for reference_area in reference_areas
        )
        findings.append(
            _result(
                "REFERENCE_AREA",
                "medium" if mismatch else "info",
                not mismatch,
                (
                    "Area does not match the corresponding reference record."
                    if mismatch
                    else "Area matches the corresponding reference record."
                ),
                {"record_value": candidate_area, "reference_values": sorted(reference_areas)}
                if mismatch
                else None,
            )
        )

    if not findings:
        findings.append(
            _result(
                "REFERENCE_MATCH",
                "info",
                True,
                "The record was compared with the matching reference entry; no comparable reference fields were available.",
            )
        )
    return findings


def validate_record(
    record: Record,
    *,
    existing_records: Iterable[Record] = (),
    reference_records: Iterable[Record] | None = None,
    field_confidences: Mapping[str, float | None] | None = None,
    config: ValidationConfig = ValidationConfig(),
) -> list[ValidationResult]:
    """Return every applicable validation finding for a land record.

    ``reference_records=None`` explicitly means reference data is unavailable;
    an empty iterable means the dataset exists but currently has no rows.
    Confidence scores use a 0–100 scale. Missing scores are never treated as
    perfect confidence.
    """
    if not isinstance(record, Mapping):
        raise TypeError("record must be a mapping")

    findings: list[ValidationResult] = []

    for field, label in _REQUIRED_FIELDS:
        present = _has_field_value(record, field)
        findings.append(
            _result(
                f"REQUIRED_{field.upper()}",
                "info" if present else "critical",
                present,
                f"{label} is present." if present else f"{label} is required.",
            )
        )

    area = _parse_area(record.get("area"))
    valid_area = area is not None and area > 0
    findings.append(
        _result(
            "AREA_POSITIVE",
            "info" if valid_area else "critical",
            valid_area,
            "Area is numeric and greater than zero."
            if valid_area
            else "Area must be a finite numeric value greater than zero.",
        )
    )

    year = _parse_year(record.get("document_year"))
    valid_year = (
        year is not None
        and config.minimum_document_year <= year <= config.maximum_document_year
    )
    findings.append(
        _result(
            "DOCUMENT_YEAR_RANGE",
            "info" if valid_year else "critical",
            valid_year,
            (
                f"Document year is within the configured range "
                f"{config.minimum_document_year}–{config.maximum_document_year}."
                if valid_year
                else f"Document year must be a year between {config.minimum_document_year} "
                f"and {config.maximum_document_year}."
            ),
            {"record_value": record.get("document_year")} if not valid_year else None,
        )
    )

    land_use = _normalize_text(record.get("land_use"))
    allowed_land_uses = {_normalize_text(value) for value in config.allowed_land_uses}
    unknown_values = {_normalize_text(value) for value in config.unknown_land_use_values}
    if land_use in allowed_land_uses:
        findings.append(
            _result("LAND_USE_ALLOWED", "info", True, "Land use is in the configured allowed list.")
        )
    elif land_use in unknown_values:
        findings.append(
            _result(
                "LAND_USE_UNKNOWN",
                "medium",
                True,
                "Land use is explicitly marked unknown and requires manual review.",
            )
        )
    else:
        findings.append(
            _result(
                "LAND_USE_ALLOWED",
                "critical",
                False,
                "Land use is not in the configured allowed list and is not marked unknown.",
                {"record_value": record.get("land_use"), "allowed_values": list(config.allowed_land_uses)},
            )
        )

    existing_rows = list(existing_records)
    findings.extend(_duplicate_checks(record, existing_rows))

    findings.extend(_reference_checks(record, reference_records, config))

    confidence = record.get("ocr_confidence")
    if confidence is None:
        findings.append(
            _result(
                "OCR_CONFIDENCE_UNAVAILABLE",
                "info",
                False,
                "OCR confidence is unavailable; it has not been assumed to be 100%.",
            )
        )
    else:
        valid_confidence = (
            not isinstance(confidence, bool)
            and isinstance(confidence, (int, float))
            and math.isfinite(confidence)
            and 0 <= confidence <= 100
        )
        low_confidence = (
            not valid_confidence
            or confidence < config.low_ocr_confidence_threshold
        )
        findings.append(
            _result(
                "OCR_CONFIDENCE",
                "medium" if low_confidence else "info",
                not low_confidence,
                (
                    f"OCR confidence is below the configured {config.low_ocr_confidence_threshold:g}/100 review threshold."
                    if low_confidence
                    else "OCR confidence meets the configured review threshold."
                ),
                {"record_value": confidence} if not valid_confidence else None,
            )
        )

    if field_confidences is None:
        findings.append(
            _result(
                "FIELD_CONFIDENCE_UNAVAILABLE",
                "info",
                False,
                "Per-field extraction confidence is unavailable; no field is assumed to be confidently extracted.",
            )
        )
    else:
        for field, label in _REQUIRED_FIELDS:
            if not _has_field_value(record, field):
                continue
            score = field_confidences.get(field)
            valid_score = (
                not isinstance(score, bool)
                and isinstance(score, (int, float))
                and math.isfinite(score)
                and 0 <= score <= 100
            )
            is_low = not valid_score or score < config.low_field_confidence_threshold
            findings.append(
                _result(
                    f"FIELD_CONFIDENCE_{field.upper()}",
                    "medium" if is_low else "info",
                    not is_low,
                    (
                        f"{label} was not confidently extracted; review it against the source document."
                        if is_low
                        else f"{label} meets the configured extraction-confidence threshold."
                    ),
                    {"record_value": record.get(field), "confidence": score}
                    if not valid_score
                    else None,
                )
            )

    return findings


def validation_result_fields(record_id: str, result: ValidationResult) -> dict[str, Any]:
    """Convert a result to the exact mapping accepted by database persistence."""
    return {
        "record_id": record_id,
        "rule_code": result["rule_code"],
        "severity": result["severity"],
        "passed": result["passed"],
        "message": result["message"],
    }
