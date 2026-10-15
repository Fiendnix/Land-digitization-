"""Shared validation, alert, review-status, and dashboard workflows."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from modules.audit import list_events, verify_chain
from modules.database import (
    DatabasePath,
    get_land_record,
    list_all_alerts,
    list_land_records,
    persist_validation_run,
    set_human_reviewed,
)
from modules.trust_score import calculate_trust_score, count_major_inconsistencies
from modules.validation import ValidationResult, validate_record

_ALERT_ACTIONS = {
    "Missing required fields": "Correct the missing field and review it against the source document.",
    "Duplicate identifiers": "Compare the records and confirm the correct parcel and survey identifiers.",
    "Reference-data mismatches": "Review the discrepancy against an authoritative reference source.",
    "Low OCR confidence": "Inspect the original document, correct uncertain fields, and confirm them manually.",
    "Geospatial anomalies": "Review parcel geometry and boundaries with the designated GIS reviewer.",
    "Low trust score": "Review the component breakdown and resolve underlying validation issues.",
    "Audit-chain integrity failures": "Stop verification-dependent actions and investigate the first invalid audit event.",
    "Data validation failures": "Correct the failed value and review it against the source document.",
}
_SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def _all_records(database_path: DatabasePath) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        batch = list_land_records(database_path, limit=1000, offset=offset)
        rows.extend(batch)
        if len(batch) < 1000:
            return rows
        offset += len(batch)


def _finding_alert(
    record_id: str, finding: Mapping[str, Any]
) -> dict[str, str] | None:
    rule_code = str(finding["rule_code"])
    if finding["passed"]:
        return None
    if rule_code.startswith("REQUIRED_"):
        category = "Missing required fields"
    elif rule_code.startswith("DUPLICATE_"):
        category = "Duplicate identifiers"
    elif rule_code.startswith("REFERENCE_") and rule_code not in {
        "REFERENCE_DATA_UNAVAILABLE",
        "REFERENCE_DATA_EMPTY",
        "REFERENCE_RECORD_NOT_FOUND",
        "REFERENCE_SELF_MATCH_SKIPPED",
    }:
        category = "Reference-data mismatches"
    elif rule_code == "OCR_CONFIDENCE" or (
        rule_code.startswith("FIELD_CONFIDENCE_")
        and rule_code != "FIELD_CONFIDENCE_UNAVAILABLE"
    ):
        category = "Low OCR confidence"
    elif (
        rule_code.startswith(("GEO_", "GEOSPATIAL_"))
        and rule_code != "GEOSPATIAL_DATA_UNAVAILABLE"
    ):
        category = "Geospatial anomalies"
    elif rule_code == "AUDIT_CHAIN_INTEGRITY":
        category = "Audit-chain integrity failures"
    elif rule_code == "TRUST_SCORE_LOW":
        category = "Low trust score"
    elif _SEVERITY_RANK.get(str(finding["severity"]), 0) > 0:
        category = "Data validation failures"
    else:
        return None
    return {
        "category": category,
        "severity": str(finding["severity"]),
        "message": str(finding["message"]),
        "suggested_action": _ALERT_ACTIONS[category],
        "issue_key": f"{record_id}:{rule_code}",
    }


def run_record_validation(
    database_path: DatabasePath,
    record_id: str,
    *,
    reference_records: Iterable[Mapping[str, Any]] | None = None,
    geospatial_findings: Iterable[ValidationResult] | None = None,
    geospatial_score: float | None = None,
) -> dict[str, Any]:
    """Run existing validators, persist findings and deduplicated alerts."""
    record = get_land_record(database_path, record_id)
    if record is None:
        raise ValueError(f"Land record {record_id!r} does not exist")
    records = _all_records(database_path)
    findings = validate_record(
        record,
        existing_records=records,
        reference_records=reference_records,
    )
    spatial_results = list(geospatial_findings or ())
    findings.extend(spatial_results)

    audit_status = verify_chain(database_path)
    if not audit_status["valid"]:
        invalid_event = audit_status["first_invalid_event"]
        location = (
            f"event {invalid_event['event_id']} at chain position "
            f"{invalid_event['chain_position']}"
            if invalid_event is not None
            else "the audit-chain head"
        )
        findings.append(
            {
                "rule_code": "AUDIT_CHAIN_INTEGRITY",
                "severity": "critical",
                "passed": False,
                "message": f"Audit-chain integrity verification failed at {location}: {audit_status['reason']}",
            }
        )

    data_findings = [
        finding
        for finding in findings
        if finding["rule_code"] != "AUDIT_CHAIN_INTEGRITY"
        and not str(finding["rule_code"]).startswith(("GEO_", "GEOSPATIAL_"))
    ]
    major_inconsistencies = count_major_inconsistencies(data_findings)
    audit_score = (
        100.0
        if audit_status["valid"] and audit_status["event_count"] > 0
        else 0.0
        if not audit_status["valid"]
        else None
    )
    trust = calculate_trust_score(
        ocr_confidence=record["ocr_confidence"],
        major_inconsistencies=major_inconsistencies,
        geospatial_score=geospatial_score,
        audit_integrity_score=audit_score,
    )
    if trust["is_complete"] and trust["score"] < 60:
        findings.append(
            {
                "rule_code": "TRUST_SCORE_LOW",
                "severity": "high",
                "passed": False,
                "message": f"Complete trust score is {trust['score']:.1f}/100, below the 60-point review threshold.",
            }
        )

    alerts = [
        alert
        for finding in findings
        if (alert := _finding_alert(record_id, finding)) is not None
    ]
    run_id = str(uuid.uuid4())
    persisted = persist_validation_run(
        database_path,
        record_id,
        run_id,
        findings,
        alerts,
    )
    return {
        **persisted,
        "findings": findings,
        "trust_score": trust,
        "audit_status": audit_status,
    }


def complete_human_review(database_path: DatabasePath, record_id: str) -> dict[str, Any]:
    """Complete explicit human review, failing if blocking conditions remain."""
    return set_human_reviewed(database_path, record_id)


def dashboard_snapshot(
    database_path: DatabasePath,
    *,
    recent_limit: int = 10,
    reference_records: Iterable[Mapping[str, Any]] | None = None,
    geospatial_scores: Mapping[str, float | None] | None = None,
) -> dict[str, Any]:
    """Calculate live dashboard counts and recent database activity."""
    if isinstance(recent_limit, bool) or not isinstance(recent_limit, int) or recent_limit < 1:
        raise ValueError("recent_limit must be a positive integer")
    records = _all_records(database_path)
    all_alerts = list_all_alerts(database_path, limit=1000)
    events = list_events(database_path)
    audit_status = verify_chain(database_path)
    counts = {
        "total": len(records),
        "pending_review": sum(record["status"] == "pending" for record in records),
        "flagged": sum(record["status"] == "flagged" for record in records),
        "verified": sum(
            record["status"] == "validated" and bool(record.get("human_reviewed"))
            for record in records
        ),
    }
    complete_scores: list[float] = []
    provisional_scores: list[float] = []
    score_categories: dict[str, int] = {
        "High confidence": 0,
        "Review recommended": 0,
        "High risk / manual review": 0,
    }
    for record in records:
        findings = validate_record(
            record,
            existing_records=records,
            reference_records=reference_records,
        )
        audit_score = (
            100.0
            if audit_status["valid"] and audit_status["event_count"] > 0
            else 0.0
            if not audit_status["valid"]
            else None
        )
        score = calculate_trust_score(
            ocr_confidence=record["ocr_confidence"],
            major_inconsistencies=count_major_inconsistencies(findings),
            geospatial_score=(
                geospatial_scores.get(record["id"])
                if geospatial_scores is not None
                else None
            ),
            audit_integrity_score=audit_score,
        )
        target = complete_scores if score["is_complete"] else provisional_scores
        target.append(score["score"])
        if score["is_complete"]:
            score_categories[score["category"]] += 1

    parcel_ids = {record["id"]: record["parcel_id"] for record in records}
    recent_alerts = [
        {
            **alert,
            "parcel_id": parcel_ids.get(alert["record_id"], alert["record_id"]),
        }
        for alert in all_alerts[:recent_limit]
    ]
    recent_activity = [
        {
            **event,
            "parcel_id": parcel_ids.get(event["record_id"], event["record_id"]),
        }
        for event in reversed(events[-recent_limit:])
    ]
    return {
        **counts,
        "average_complete_trust_score": (
            sum(complete_scores) / len(complete_scores) if complete_scores else None
        ),
        "average_provisional_subtotal": (
            sum(provisional_scores) / len(provisional_scores) if provisional_scores else None
        ),
        "complete_score_count": len(complete_scores),
        "provisional_score_count": len(provisional_scores),
        "trust_score_categories": score_categories,
        "recent_alerts": recent_alerts,
        "recent_activity": recent_activity,
    }
