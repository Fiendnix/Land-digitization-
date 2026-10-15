"""Explainable, deterministic software verification indicator.

This score is not a probability of legal ownership, a legal certificate, or
proof of fraud. Missing components contribute no weighted subtotal and are
reported explicitly; weights are never redistributed.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Any, TypedDict

OCR_WEIGHT = 0.30
DATA_CONSISTENCY_WEIGHT = 0.30
GEOSPATIAL_WEIGHT = 0.25
AUDIT_INTEGRITY_WEIGHT = 0.15

DEFAULT_WEIGHTS: Mapping[str, float] = MappingProxyType(
    {
        "O": OCR_WEIGHT,
        "D": DATA_CONSISTENCY_WEIGHT,
        "G": GEOSPATIAL_WEIGHT,
        "H": AUDIT_INTEGRITY_WEIGHT,
    }
)
_COMPONENT_LABELS = {
    "O": "OCR extraction confidence",
    "D": "Data consistency",
    "G": "Geospatial validation",
    "H": "Audit-chain integrity",
}


class ScoreComponent(TypedDict):
    label: str
    weight: float
    input_score: float | None
    weighted_contribution: float
    available: bool


class TrustScoreResult(TypedDict):
    score: float
    status: str
    is_complete: bool
    category: str
    components: dict[str, ScoreComponent]
    missing_components: list[str]
    major_inconsistencies: int
    data_consistency_calculation: str
    explanation: str


def validate_weights(weights: Mapping[str, float]) -> None:
    """Validate that exactly the four named component weights sum to one."""
    if set(weights) != set(DEFAULT_WEIGHTS):
        raise ValueError("weights must define exactly O, D, G, and H")
    for component, weight in weights.items():
        if isinstance(weight, bool) or not isinstance(weight, (int, float)):
            raise TypeError(f"weight {component} must be numeric")
        if not math.isfinite(weight) or weight < 0:
            raise ValueError(f"weight {component} must be finite and non-negative")
    if not math.isclose(sum(weights.values()), 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("trust-score weights must sum to 1.0")


validate_weights(DEFAULT_WEIGHTS)


def calculate_data_consistency(major_inconsistencies: int) -> float:
    """Start at 100 and deduct 25 points per major inconsistency, floor zero."""
    if isinstance(major_inconsistencies, bool) or not isinstance(major_inconsistencies, int):
        raise TypeError("major_inconsistencies must be a non-negative integer")
    if major_inconsistencies < 0:
        raise ValueError("major_inconsistencies must be a non-negative integer")
    return float(max(0, 100 - 25 * major_inconsistencies))


def count_major_inconsistencies(findings: Iterable[Mapping[str, Any]]) -> int:
    """Count distinct failed critical validation issues, not repeated rules."""
    unique_issues: set[str] = set()
    for finding in findings:
        if not isinstance(finding, Mapping):
            raise TypeError("each finding must be a mapping")
        if finding.get("passed") is not False or finding.get("severity") != "critical":
            continue
        rule_code = finding.get("rule_code")
        if not isinstance(rule_code, str) or not rule_code:
            raise ValueError("critical findings must have a rule_code")
        if rule_code in {"AREA_POSITIVE", "REQUIRED_AREA"}:
            issue = "AREA"
        elif rule_code in {"LAND_USE_ALLOWED", "REQUIRED_LAND_USE"}:
            issue = "LAND_USE"
        else:
            issue = rule_code
        unique_issues.add(issue)
    return len(unique_issues)


def _validated_score(name: str, value: float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} score must be numeric or None")
    if not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError(f"{name} score must be between 0 and 100")
    return float(value)


def _category(score: float) -> str:
    if score >= 80:
        return "High confidence"
    if score >= 60:
        return "Review recommended"
    return "High risk / manual review"


def calculate_trust_score(
    *,
    ocr_confidence: float | None,
    major_inconsistencies: int,
    geospatial_score: float | None = None,
    audit_integrity_score: float | None = None,
    weights: Mapping[str, float] = DEFAULT_WEIGHTS,
) -> TrustScoreResult:
    """Calculate a full score or an explicitly provisional fixed-weight subtotal.

    For provisional output, available components retain their original
    weights, unavailable components contribute 0 to the displayed subtotal,
    and the subtotal is not assigned a confidence category. This is not a
    substitute for the missing component scores.
    """
    validate_weights(weights)
    data_consistency = calculate_data_consistency(major_inconsistencies)
    inputs = {
        "O": _validated_score("OCR", ocr_confidence),
        "D": data_consistency,
        "G": _validated_score("geospatial", geospatial_score),
        "H": _validated_score("audit integrity", audit_integrity_score),
    }

    components: dict[str, ScoreComponent] = {}
    missing: list[str] = []
    subtotal = 0.0
    for key in ("O", "D", "G", "H"):
        score = inputs[key]
        available = score is not None
        contribution = score * weights[key] if score is not None else 0.0
        subtotal += contribution
        if not available:
            missing.append(key)
        components[key] = {
            "label": _COMPONENT_LABELS[key],
            "weight": float(weights[key]),
            "input_score": score,
            "weighted_contribution": contribution,
            "available": available,
        }

    clamped_subtotal = min(100.0, max(0.0, subtotal))
    displayed_score = round(clamped_subtotal, 1)
    is_complete = not missing
    return {
        "score": displayed_score,
        "status": "complete" if is_complete else "provisional",
        "is_complete": is_complete,
        "category": _category(displayed_score) if is_complete else "Provisional (incomplete)",
        "components": components,
        "missing_components": missing,
        "major_inconsistencies": major_inconsistencies,
        "data_consistency_calculation": (
            f"D = max(0, 100 - 25 × {major_inconsistencies}) = {data_consistency:.1f}"
        ),
        "explanation": (
            "All four components are available; the weighted score is complete."
            if is_complete
            else (
                "Provisional fixed-weight subtotal only: unavailable components are "
                "not assigned scores and their weights are not redistributed. "
                "This subtotal is not a complete trust score."
            )
        ),
    }
