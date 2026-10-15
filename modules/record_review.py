"""Validation helpers for user-reviewed OCR record fields."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

_REQUIRED_TEXT_FIELDS = {
    "parcel_id": ("Parcel ID", 100),
    "survey_number": ("Survey number", 100),
    "owner_name": ("Owner name", 200),
    "district": ("District", 120),
    "village": ("Village", 120),
    "land_use": ("Land use", 80),
}
_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
_MAX_AREA = 1_000_000_000_000


def prepare_reviewed_record(
    values: Mapping[str, Any],
    *,
    ocr_confidence: float | None,
    source_document_hash: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Build a database payload and field-keyed validation errors.

    This validates only record input shape and basic limits. It does not
    perform land validation, ownership checks, or trust scoring.
    """
    payload: dict[str, Any] = {}
    errors: dict[str, str] = {}

    record_id = values.get("id", "")
    if not isinstance(record_id, str):
        errors["id"] = "Record ID must be text."
    elif record_id.strip():
        record_id = record_id.strip()
        if len(record_id) > 64:
            errors["id"] = "Record ID must be no longer than 64 characters."
        else:
            payload["id"] = record_id

    for key, (label, max_length) in _REQUIRED_TEXT_FIELDS.items():
        value = values.get(key)
        if not isinstance(value, str) or not value.strip():
            errors[key] = f"{label} is required."
        elif len(value.strip()) > max_length:
            errors[key] = f"{label} must be no longer than {max_length} characters."
        else:
            payload[key] = value.strip()

    raw_area = values.get("area")
    if not isinstance(raw_area, str) or not raw_area.strip():
        errors["area"] = "Area is required."
    else:
        try:
            area = float(raw_area.strip().replace(",", ""))
        except ValueError:
            errors["area"] = "Enter a numeric area value."
        else:
            if not math.isfinite(area) or not 0 < area <= _MAX_AREA:
                errors["area"] = "Area must be greater than 0 and no more than 1e12."
            else:
                payload["area"] = area

    area_unit = values.get("area_unit", "")
    if area_unit is not None and str(area_unit).strip():
        unit_text = str(area_unit).strip()
        if len(unit_text) > 50:
            errors["area_unit"] = "Area unit must be no longer than 50 characters."
        else:
            payload["area_unit"] = unit_text

    raw_year = values.get("document_year")
    if not isinstance(raw_year, str) or not raw_year.strip():
        errors["document_year"] = "Document year is required."
    elif not re.fullmatch(r"\d{4}", raw_year.strip()):
        errors["document_year"] = "Enter a four-digit year."
    else:
        year = int(raw_year)
        if not 1000 <= year <= 9999:
            errors["document_year"] = "Year must be between 1000 and 9999."
        else:
            payload["document_year"] = year

    if ocr_confidence is not None:
        if (
            isinstance(ocr_confidence, bool)
            or not isinstance(ocr_confidence, (int, float))
            or not math.isfinite(ocr_confidence)
            or not 0 <= ocr_confidence <= 100
        ):
            errors["ocr_confidence"] = "OCR confidence must be between 0 and 100."
        else:
            payload["ocr_confidence"] = float(ocr_confidence)
    else:
        payload["ocr_confidence"] = None

    if not isinstance(source_document_hash, str) or not _SHA256_PATTERN.fullmatch(
        source_document_hash
    ):
        errors["source_document_hash"] = "Source document hash must be a SHA-256 digest."
    else:
        payload["source_document_hash"] = source_document_hash.lower()

    return payload, errors
