"""Deterministic checks and map helpers for illustrative parcel GeoJSON."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NotRequired, TypedDict

from shapely.errors import GEOSException
from shapely.geometry import shape
from shapely.validation import explain_validity

Feature = Mapping[str, Any]
FeatureCollection = Mapping[str, Any]


class GeospatialOutcome(TypedDict):
    score: float | None
    findings: list[dict[str, Any]]
    parcel_id: str | None
    geometry_status: str
    details: NotRequired[str]


def load_geojson(path: str | Path) -> dict[str, Any]:
    """Load and minimally validate a GeoJSON FeatureCollection."""
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Could not load parcel GeoJSON from {path}: {error}") from error
    if not isinstance(value, dict) or value.get("type") != "FeatureCollection":
        raise ValueError("Parcel GeoJSON must be a FeatureCollection.")
    features = value.get("features")
    if not isinstance(features, list):
        raise ValueError("Parcel GeoJSON features must be a list.")
    for index, feature in enumerate(features):
        if (
            not isinstance(feature, dict)
            or feature.get("type") != "Feature"
            or not isinstance(feature.get("properties"), dict)
        ):
            raise ValueError(f"GeoJSON feature {index + 1} is malformed.")
    return value


def _feature_identity(feature: Feature) -> tuple[str, str]:
    properties = feature["properties"]
    record_id = properties.get("record_id")
    parcel_id = properties.get("parcel_id")
    return (
        str(record_id).strip() if record_id is not None else "",
        str(parcel_id).strip() if parcel_id is not None else "",
    )


def _geo_finding(
    code: str, severity: str, passed: bool, message: str
) -> dict[str, Any]:
    return {
        "rule_code": code,
        "severity": severity,
        "passed": passed,
        "message": message,
    }


def evaluate_geospatial_records(
    records: Sequence[Mapping[str, Any]], feature_collection: FeatureCollection
) -> dict[str, GeospatialOutcome]:
    """Validate geometry and overlaps, returning outcomes keyed by record ID.

    Score policy for this demonstration: 100 for a valid non-overlapping
    polygon, 50 for a valid polygon overlapping another parcel, and 0 for an
    invalid or unsupported polygon. Missing geometry yields no score.
    """
    features = feature_collection.get("features")
    if not isinstance(features, list):
        raise ValueError("GeoJSON features must be a list.")

    by_record_id: dict[str, Feature] = {}
    by_parcel_id: dict[str, list[Feature]] = {}
    for index, feature in enumerate(features):
        if not isinstance(feature, Mapping) or not isinstance(
            feature.get("properties"), Mapping
        ):
            raise ValueError(f"GeoJSON feature {index + 1} is malformed.")
        record_id, parcel_id = _feature_identity(feature)
        if record_id:
            if record_id in by_record_id:
                raise ValueError(f"Duplicate GeoJSON record_id {record_id!r}.")
            by_record_id[record_id] = feature
        if parcel_id:
            by_parcel_id.setdefault(parcel_id.casefold(), []).append(feature)

    parsed: dict[int, tuple[Any | None, str | None]] = {}
    for index, feature in enumerate(features):
        raw_geometry = feature.get("geometry")
        if raw_geometry is None:
            parsed[index] = (None, None)
            continue
        try:
            geometry = shape(raw_geometry)
        except (GEOSException, TypeError, ValueError) as error:
            parsed[index] = (None, f"Geometry could not be read: {error}")
            continue
        if geometry.is_empty:
            parsed[index] = (geometry, "Geometry is empty.")
        elif geometry.geom_type not in {"Polygon", "MultiPolygon"}:
            parsed[index] = (
                geometry,
                f"Expected Polygon or MultiPolygon, got {geometry.geom_type}.",
            )
        elif not geometry.is_valid:
            parsed[index] = (geometry, explain_validity(geometry))
        else:
            parsed[index] = (geometry, None)

    overlapping_indices: set[int] = set()
    valid_indices = [
        index
        for index, (geometry, issue) in parsed.items()
        if geometry is not None
        and geometry.geom_type in {"Polygon", "MultiPolygon"}
        and geometry.is_valid
        and not geometry.is_empty
        and issue is None
    ]
    for offset, left_index in enumerate(valid_indices):
        left_geometry = parsed[left_index][0]
        left_parcel_id = _feature_identity(features[left_index])[1].casefold()
        for right_index in valid_indices[offset + 1 :]:
            right_parcel_id = _feature_identity(features[right_index])[1].casefold()
            if left_parcel_id and left_parcel_id == right_parcel_id:
                continue
            try:
                overlaps = left_geometry.intersection(parsed[right_index][0]).area > 0
            except GEOSException:
                overlaps = False
            if overlaps:
                overlapping_indices.update((left_index, right_index))

    feature_indexes = {id(feature): index for index, feature in enumerate(features)}
    outcomes: dict[str, GeospatialOutcome] = {}
    for record in records:
        record_id = str(record.get("id", "")).strip()
        parcel_id_value = str(record.get("parcel_id", "")).strip()
        if not record_id:
            raise ValueError("Every record requires a stable id for geospatial validation.")
        feature = by_record_id.get(record_id)
        if feature is None and parcel_id_value:
            matches = by_parcel_id.get(parcel_id_value.casefold(), [])
            if len(matches) > 1:
                raise ValueError(
                    f"Multiple GeoJSON features match parcel_id {parcel_id_value!r}."
                )
            feature = matches[0] if matches else None
        if feature is None:
            outcomes[record_id] = {
                "score": None,
                "findings": [
                    _geo_finding(
                        "GEOSPATIAL_DATA_UNAVAILABLE",
                        "info",
                        False,
                        "No sample geometry matches this record; spatial validation is unavailable.",
                    )
                ],
                "parcel_id": parcel_id_value or None,
                "geometry_status": "unavailable",
            }
            continue

        index = feature_indexes[id(feature)]
        geometry, issue = parsed[index]
        parcel_label = parcel_id_value or _feature_identity(feature)[1] or record_id
        if geometry is None and issue is None:
            outcomes[record_id] = {
                "score": None,
                "findings": [
                    _geo_finding(
                        "GEOSPATIAL_DATA_UNAVAILABLE",
                        "info",
                        False,
                        f"No geometry is available for {parcel_label}; spatial validation is unavailable.",
                    )
                ],
                "parcel_id": parcel_label,
                "geometry_status": "unavailable",
            }
            continue
        if issue is not None or geometry is None or geometry.is_empty:
            explanation = issue or "Geometry is missing."
            outcomes[record_id] = {
                "score": 0.0,
                "findings": [
                    _geo_finding(
                        "GEOSPATIAL_INVALID_GEOMETRY",
                        "critical",
                        False,
                        f"Geometry for {parcel_label} is invalid: {explanation}",
                    )
                ],
                "parcel_id": parcel_label,
                "geometry_status": "invalid",
                "details": explanation,
            }
        elif index in overlapping_indices:
            outcomes[record_id] = {
                "score": 50.0,
                "findings": [
                    _geo_finding(
                        "GEOSPATIAL_OVERLAP",
                        "medium",
                        False,
                        f"Geometry for {parcel_label} overlaps another sample parcel; review boundaries.",
                    )
                ],
                "parcel_id": parcel_label,
                "geometry_status": "overlap",
            }
        else:
            outcomes[record_id] = {
                "score": 100.0,
                "findings": [
                    _geo_finding(
                        "GEOSPATIAL_GEOMETRY_VALID",
                        "info",
                        True,
                        f"Geometry for {parcel_label} is valid and does not overlap another sample parcel.",
                    )
                ],
                "parcel_id": parcel_label,
                "geometry_status": "valid",
            }
    return outcomes


def styled_geojson(
    feature_collection: FeatureCollection,
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return GeoJSON features styled by actual validation outcomes."""
    outcomes = evaluate_geospatial_records(records, feature_collection)
    output = json.loads(json.dumps(feature_collection))
    colors = {"valid": "#26734d", "overlap": "#c77b18", "invalid": "#a43737"}
    for feature in output["features"]:
        properties = feature["properties"]
        outcome = outcomes.get(properties.get("record_id"))
        status = outcome["geometry_status"] if outcome else "unavailable"
        properties["geometry_status"] = status
        properties["review_label"] = (
            status.title() if status != "unavailable" else "No linked record"
        )
        feature["properties"]["map_color"] = colors.get(status, "#718096")
    return output
