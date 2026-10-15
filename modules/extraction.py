"""Deterministic extraction of land-record fields from OCR text.

The field-confidence scores are rule-match strength estimates on a 0–100
scale, not probabilities or OCR-engine confidence. Missing fields and missing
scores remain ``None`` / absent; no value is inferred from unrelated text.
The initial rules recognize common English labels and familiar land-record
terms such as Khasra, Khata, and Mouza. They are intentionally conservative.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_FIELD_LABELS = (
    "parcel\\s*(?:id|no\\.?|number|#)?",
    "plot\\s*(?:id|no\\.?|number|#)?",
    "survey\\s*(?:no\\.?|number|#)?",
    "khasra\\s*(?:no\\.?|number|#)?",
    "khata\\s*(?:no\\.?|number|#)?",
    "owner(?:'s)?\\s*name",
    "name\\s+of\\s+(?:the\\s+)?owner",
    "proprietor",
    "holder\\s*name",
    "district",
    "tehsil",
    "village",
    "gram",
    "mouza",
    "land\\s*use(?:\\s*type)?",
    "type\\s+of\\s+land",
    "total\\s+area",
    "area",
    "document\\s*year",
    "year",
    "dated?",
    "date",
    "dt\\.?",
)
_LABEL_START = re.compile(
    rf"^(?:{'|'.join(_FIELD_LABELS)})(?:\s*[:#=-]\s*|\s+)", re.IGNORECASE
)
_LABEL_ONLY = re.compile(rf"^(?:{'|'.join(_FIELD_LABELS)})\s*$", re.IGNORECASE)
_NEXT_LABEL = re.compile(
    rf"\s+(?=(?:{'|'.join(_FIELD_LABELS)})(?:\s*[:#=-]\s*|\s+))",
    re.IGNORECASE,
)


@dataclass
class ExtractionResult:
    """Structured fields; values and optional rule-match scores."""

    parcel_id: str | None = None
    survey_number: str | None = None
    owner_name: str | None = None
    district: str | None = None
    village: str | None = None
    land_use: str | None = None
    area: float | None = None
    area_unit: str | None = None
    document_year: int | None = None
    field_confidences: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "parcel_id": self.parcel_id,
            "survey_number": self.survey_number,
            "owner_name": self.owner_name,
            "district": self.district,
            "village": self.village,
            "land_use": self.land_use,
            "area": self.area,
            "area_unit": self.area_unit,
            "document_year": self.document_year,
            "field_confidences": dict(self.field_confidences),
        }

    @property
    def recognized_count(self) -> int:
        return sum(
            getattr(self, name) is not None
            for name in (
                "parcel_id", "survey_number", "owner_name", "district",
                "village", "land_use", "area", "document_year",
            )
        )

    @property
    def total_fields(self) -> int:
        return 8


def _label_value(text: str, labels: str) -> tuple[str | None, float | None]:
    """Find a labelled value on its line, or on the immediately following line."""
    pattern = re.compile(
        rf"(?<![\w])(?:{labels})(?:\s*[:#=-]\s*|\s+|$)([^\r\n]*)",
        re.IGNORECASE,
    )
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = pattern.search(line)
        if not match:
            continue
        value = match.group(1).strip().strip(" \t:;,-")
        if value:
            next_label = _NEXT_LABEL.search(value)
            if next_label:
                value = value[: next_label.start()].strip().strip(" \t:;,-")
            if value:
                return value, 85.0
        if index + 1 < len(lines):
            candidate = lines[index + 1].strip().strip(" \t:;,-")
            if candidate and not _LABEL_START.match(candidate) and not _LABEL_ONLY.match(candidate):
                return candidate, 65.0
    return None, None


def _capture(text: str, labels: str, value_pattern: str) -> tuple[str | None, float | None]:
    value, confidence = _label_value(text, labels)
    if value is None:
        return None, None
    match = re.match(value_pattern, value, re.IGNORECASE)
    if not match:
        return None, None
    normalized = match.group(1).strip().rstrip(".,;:")
    return (normalized, confidence) if normalized else (None, None)


def _extract_area(text: str) -> tuple[float | None, str | None, float | None]:
    raw, confidence = _label_value(text, r"(?:total\s+)?area")
    if raw is None:
        return None, None, None
    number = re.match(r"^([+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\b", raw)
    if not number:
        return None, None, None
    try:
        area = float(number.group(1).replace(",", ""))
    except ValueError:
        return None, None, None
    if area <= 0:
        return None, None, None
    unit = raw[number.end():].strip().strip(" ,.;:-") or None
    return area, unit, confidence


def _extract_year(text: str) -> tuple[int | None, float | None]:
    raw, confidence = _label_value(
        text, r"(?:document\s+year|year|date|dated|dt\.?)"
    )
    if raw is None:
        return None, None

    # Search any common order of day/month/year, ISO-like dates, and textual
    # month dates. Only the four-digit year is returned.
    match = re.search(r"(?<!\d)((?:18|19|20|21)\d{2})(?!\d)", raw)
    if not match:
        return None, None
    year = int(match.group(1))
    if not 1800 <= year <= 2199:
        return None, None
    return year, confidence


def extract_fields(text: str) -> ExtractionResult:
    """Extract fields from OCR text; unrecognized values stay ``None``."""
    if not isinstance(text, str):
        raise TypeError("OCR text must be a string")
    result = ExtractionResult()
    if not text.strip():
        return result

    confidence: dict[str, float] = {}

    result.parcel_id, score = _capture(
        text,
        r"(?:parcel\s*(?:id|no\.?|number|#)|plot\s*(?:id|no\.?|number|#))",
        r"([\w./-]{2,64})",
    )
    if result.parcel_id and score is not None:
        confidence["parcel_id"] = score

    result.survey_number, score = _capture(
        text,
        r"(?:survey\s*(?:no\.?|number|#)?|khasra\s*(?:no\.?|number|#)?|khata\s*(?:no\.?|number|#)?)",
        r"([\w./-]{1,64})",
    )
    if result.survey_number and score is not None:
        confidence["survey_number"] = score

    result.owner_name, score = _capture(
        text,
        r"(?:owner(?:'s)?\s*name|name\s+of\s+(?:the\s+)?owner|proprietor|holder\s*name)",
        r"([\w][\w\s.'’()-]{1,100})",
    )
    if result.owner_name and score is not None:
        result.owner_name = result.owner_name.strip()
        confidence["owner_name"] = score

    for field_name, labels in (
        ("district", r"(?:district|tehsil)"),
        ("village", r"(?:village|gram|mouza)"),
    ):
        value, score = _capture(text, labels, r"([\w][\w\s.'’()-]{1,80})")
        if value:
            setattr(result, field_name, value.strip())
            if score is not None:
                confidence[field_name] = score

    result.land_use, score = _capture(
        text,
        r"(?:land\s*use(?:\s*type)?|type\s+of\s+land)",
        r"([\w][\w\s-]{1,80})",
    )
    if result.land_use and score is not None:
        confidence["land_use"] = score

    # Keyword fallback is deliberately weaker and only returns a known term.
    if result.land_use is None:
        keyword = re.search(
            r"\b(agricultural|agriculture|residential|commercial|industrial|"
            r"forest|barren|wasteland|wetland|grazing|plantation|orchard|"
            r"garden|institutional|recreational|mixed)\b",
            text,
            re.IGNORECASE,
        )
        if keyword:
            result.land_use = keyword.group(1).title()
            confidence["land_use"] = 50.0

    result.area, result.area_unit, score = _extract_area(text)
    if result.area is not None and score is not None:
        confidence["area"] = score
        if result.area_unit is not None:
            confidence["area_unit"] = score

    result.document_year, score = _extract_year(text)
    if result.document_year is not None and score is not None:
        confidence["document_year"] = score

    result.field_confidences = confidence
    return result
