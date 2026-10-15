"""SQLite persistence for land records and their related results/events/alerts.

All database functions take an explicit path and open their own connection.
Foreign-key enforcement is enabled for every connection. Timestamps are stored
as UTC ISO 8601 strings.
"""

from __future__ import annotations

import math
import sqlite3
import uuid
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

DatabasePath = str | Path
Row = dict[str, Any]

_RECORD_FIELDS = {
    "parcel_id": ("text", 100),
    "survey_number": ("text", 100),
    "owner_name": ("text", 200),
    "district": ("text", 120),
    "village": ("text", 120),
    "land_use": ("text", 80),
    "area": ("area", None),
    "area_unit": ("optional_text", 50),
    "document_year": ("year", None),
    "ocr_confidence": ("score", None),
    "trust_score": ("score", None),
    "status": ("status", None),
    "source_document_hash": ("hash", None),
}
_REQUIRED_RECORD_FIELDS = {
    "parcel_id",
    "survey_number",
    "owner_name",
    "district",
    "village",
    "land_use",
    "area",
    "document_year",
}
_SEVERITIES = {"info", "low", "medium", "high", "critical"}
_STATUSES = {"pending", "validated", "flagged"}
_VALIDATION_FIELDS = {"record_id", "rule_code", "severity", "passed", "message"}
_ALERT_FIELDS = {"record_id", "category", "severity", "message"}
_AUDIT_FIELDS = {
    "record_id",
    "action",
    "event_timestamp",
    "previous_hash",
    "event_hash",
    "payload",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS land_records (
    id TEXT PRIMARY KEY,
    parcel_id TEXT NOT NULL CHECK (length(trim(parcel_id)) BETWEEN 1 AND 100),
    survey_number TEXT NOT NULL CHECK (length(trim(survey_number)) BETWEEN 1 AND 100),
    owner_name TEXT NOT NULL CHECK (length(trim(owner_name)) BETWEEN 1 AND 200),
    district TEXT NOT NULL CHECK (length(trim(district)) BETWEEN 1 AND 120),
    village TEXT NOT NULL CHECK (length(trim(village)) BETWEEN 1 AND 120),
    land_use TEXT NOT NULL CHECK (length(trim(land_use)) BETWEEN 1 AND 80),
    area REAL NOT NULL CHECK (area > 0 AND area <= 1000000000000),
    area_unit TEXT CHECK (area_unit IS NULL OR length(trim(area_unit)) BETWEEN 1 AND 50),
    document_year INTEGER NOT NULL CHECK (document_year BETWEEN 1000 AND 9999),
    ocr_confidence REAL CHECK (ocr_confidence IS NULL OR ocr_confidence BETWEEN 0 AND 100),
    trust_score REAL CHECK (trust_score IS NULL OR trust_score BETWEEN 0 AND 100),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'validated', 'flagged')),
    source_document_hash TEXT
        CHECK (
            source_document_hash IS NULL OR (
                length(source_document_hash) = 64
                AND source_document_hash NOT GLOB '*[^0-9a-fA-F]*'
            )
        ),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
    ,human_reviewed INTEGER NOT NULL DEFAULT 0 CHECK (human_reviewed IN (0, 1))
    ,reviewed_at TEXT
    ,record_revision INTEGER NOT NULL DEFAULT 0
    ,validation_record_revision INTEGER
);

CREATE TABLE IF NOT EXISTS validation_results (
    id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL REFERENCES land_records(id) ON DELETE CASCADE,
    rule_code TEXT NOT NULL CHECK (length(trim(rule_code)) BETWEEN 1 AND 100),
    severity TEXT NOT NULL CHECK (severity IN ('info', 'low', 'medium', 'high', 'critical')),
    passed INTEGER NOT NULL CHECK (passed IN (0, 1)),
    message TEXT NOT NULL CHECK (length(trim(message)) BETWEEN 1 AND 2000),
    created_at TEXT NOT NULL,
    run_id TEXT
);

CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL REFERENCES land_records(id) ON DELETE CASCADE,
    action TEXT NOT NULL CHECK (length(trim(action)) BETWEEN 1 AND 100),
    event_timestamp TEXT NOT NULL,
    previous_hash TEXT,
    event_hash TEXT,
    canonical_payload TEXT NOT NULL CHECK (json_valid(canonical_payload)),
    chain_position INTEGER UNIQUE,
    operation_key TEXT UNIQUE,
    CHECK (previous_hash IS NULL OR (
        length(previous_hash) = 64 AND previous_hash NOT GLOB '*[^0-9a-fA-F]*'
    )),
    CHECK (event_hash IS NULL OR (
        length(event_hash) = 64 AND event_hash NOT GLOB '*[^0-9a-fA-F]*'
    ))
);

CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL REFERENCES land_records(id) ON DELETE CASCADE,
    category TEXT NOT NULL CHECK (length(trim(category)) BETWEEN 1 AND 100),
    severity TEXT NOT NULL CHECK (severity IN ('info', 'low', 'medium', 'high', 'critical')),
    message TEXT NOT NULL CHECK (length(trim(message)) BETWEEN 1 AND 2000),
    resolved INTEGER NOT NULL DEFAULT 0 CHECK (resolved IN (0, 1)),
    created_at TEXT NOT NULL,
    suggested_action TEXT NOT NULL DEFAULT 'Review the record and source document.',
    issue_key TEXT
);

CREATE INDEX IF NOT EXISTS idx_validation_results_record_id
    ON validation_results(record_id);
CREATE INDEX IF NOT EXISTS idx_audit_events_record_id
    ON audit_events(record_id);
CREATE INDEX IF NOT EXISTS idx_alerts_record_id
    ON alerts(record_id);
CREATE TABLE IF NOT EXISTS audit_chain_state (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    last_position INTEGER NOT NULL,
    last_hash TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _new_id() -> str:
    return str(uuid.uuid4())


@contextmanager
def _connect(database_path: DatabasePath) -> Iterator[sqlite3.Connection]:
    path = str(database_path)
    if path != ":memory:":
        Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
    finally:
        connection.close()


def _as_dict(row: sqlite3.Row | None) -> Row | None:
    return dict(row) if row is not None else None


def _require_mapping(value: Mapping[str, Any], label: str) -> None:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be a mapping")


def _validate_text(name: str, value: Any, max_length: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip() or len(value) > max_length:
        raise ValueError(f"{name} must contain 1 to {max_length} characters")
    return value.strip()


def _validate_record_fields(fields: Mapping[str, Any], *, partial: bool) -> dict[str, Any]:
    _require_mapping(fields, "record fields")
    unknown = set(fields) - set(_RECORD_FIELDS)
    if unknown:
        raise ValueError(f"Unsupported land-record field(s): {', '.join(sorted(unknown))}")
    if not partial:
        missing = _REQUIRED_RECORD_FIELDS - set(fields)
        if missing:
            raise ValueError(f"Missing required field(s): {', '.join(sorted(missing))}")
    validated: dict[str, Any] = {}
    for name, value in fields.items():
        kind, limit = _RECORD_FIELDS[name]
        if value is None and name in {"ocr_confidence", "trust_score", "source_document_hash"}:
            validated[name] = None
        elif kind == "text":
            validated[name] = _validate_text(name, value, limit or 0)
        elif kind == "optional_text":
            validated[name] = (
                None if value is None or (isinstance(value, str) and not value.strip())
                else _validate_text(name, value, limit or 0)
            )
        elif kind == "area":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError("area must be a number")
            if not math.isfinite(value) or not 0 < value <= 1_000_000_000_000:
                raise ValueError("area must be finite, greater than 0, and at most 1e12")
            validated[name] = float(value)
        elif kind == "year":
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError("document_year must be an integer")
            if not 1000 <= value <= 9999:
                raise ValueError("document_year must be between 1000 and 9999")
            validated[name] = value
        elif kind == "score":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{name} must be a number or None")
            if not math.isfinite(value) or not 0 <= value <= 100:
                raise ValueError(f"{name} must be finite and between 0 and 100")
            validated[name] = float(value)
        elif kind == "status":
            status = _validate_text(name, value, 20)
            if status not in _STATUSES:
                raise ValueError(f"status must be one of {', '.join(sorted(_STATUSES))}")
            validated[name] = status
        elif kind == "hash":
            if not isinstance(value, str):
                raise TypeError("source_document_hash must be a string or None")
            if len(value) != 64 or any(char not in "0123456789abcdefABCDEF" for char in value):
                raise ValueError("source_document_hash must be a 64-character SHA-256 hex digest")
            validated[name] = value.lower()
    return validated


def _derive_record_status(
    connection: sqlite3.Connection,
    record_id: str,
    *,
    latest_findings: list[Mapping[str, Any]] | None = None,
    human_reviewed: bool | None = None,
) -> str:
    unresolved = connection.execute(
        """SELECT 1 FROM alerts
           WHERE record_id = ? AND resolved = 0
             AND severity IN ('high', 'critical') LIMIT 1""",
        (record_id,),
    ).fetchone()
    if latest_findings is None:
        latest_run = connection.execute(
            """SELECT run_id FROM validation_results
               WHERE record_id = ? ORDER BY rowid DESC LIMIT 1""",
            (record_id,),
        ).fetchone()
        run_id = latest_run["run_id"] if latest_run is not None else None
        query = "SELECT severity, passed FROM validation_results WHERE record_id = ?"
        parameters: tuple[Any, ...] = (record_id,)
        if run_id is not None:
            query += " AND run_id = ?"
            parameters = (record_id, run_id)
        latest_findings = [
            {"severity": row["severity"], "passed": bool(row["passed"])}
            for row in connection.execute(query, parameters).fetchall()
        ]
    critical_failure = any(
        finding.get("severity") in {"high", "critical"}
        and finding.get("passed") is False
        for finding in latest_findings
    )
    record = connection.execute(
        "SELECT human_reviewed FROM land_records WHERE id = ?", (record_id,)
    ).fetchone()
    if record is None:
        raise ValueError(f"Land record {record_id!r} does not exist")
    is_reviewed = bool(record["human_reviewed"]) if human_reviewed is None else human_reviewed
    if unresolved is not None or critical_failure:
        return "flagged"
    return "validated" if is_reviewed else "pending"


def _validate_severity(value: Any) -> str:
    severity = _validate_text("severity", value, 20)
    if severity not in _SEVERITIES:
        raise ValueError(f"severity must be one of {', '.join(sorted(_SEVERITIES))}")
    return severity


def initialize_database(database_path: DatabasePath) -> None:
    """Create the schema if absent; safe to call repeatedly on an existing DB."""
    with _connect(database_path) as connection:
        connection.executescript(_SCHEMA)
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(land_records)").fetchall()
        }
        if "area_unit" not in columns:
            connection.execute("ALTER TABLE land_records ADD COLUMN area_unit TEXT")
        audit_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(audit_events)").fetchall()
        }
        if "chain_position" not in audit_columns:
            connection.execute("ALTER TABLE audit_events ADD COLUMN chain_position INTEGER")
        if "operation_key" not in audit_columns:
            connection.execute("ALTER TABLE audit_events ADD COLUMN operation_key TEXT")
        record_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(land_records)").fetchall()
        }
        if "human_reviewed" not in record_columns:
            connection.execute(
                "ALTER TABLE land_records ADD COLUMN human_reviewed INTEGER NOT NULL DEFAULT 0"
            )
            if "status" in record_columns:
                connection.execute(
                    "UPDATE land_records SET status = 'pending' WHERE status = 'validated'"
                )
        if "reviewed_at" not in record_columns:
            connection.execute("ALTER TABLE land_records ADD COLUMN reviewed_at TEXT")
        if "record_revision" not in record_columns:
            connection.execute(
                "ALTER TABLE land_records ADD COLUMN record_revision INTEGER NOT NULL DEFAULT 0"
            )
        if "validation_record_revision" not in record_columns:
            connection.execute(
                "ALTER TABLE land_records ADD COLUMN validation_record_revision INTEGER"
            )
        if "status" in record_columns and "human_reviewed" in record_columns:
            connection.execute(
                """UPDATE land_records SET status = 'pending'
                   WHERE status = 'validated' AND human_reviewed = 0"""
            )
        validation_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(validation_results)"
            ).fetchall()
        }
        if "run_id" not in validation_columns:
            connection.execute("ALTER TABLE validation_results ADD COLUMN run_id TEXT")
        alert_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(alerts)").fetchall()
        }
        if "suggested_action" not in alert_columns:
            connection.execute(
                "ALTER TABLE alerts ADD COLUMN suggested_action TEXT NOT NULL "
                "DEFAULT 'Review the record and source document.'"
            )
        if "issue_key" not in alert_columns:
            connection.execute("ALTER TABLE alerts ADD COLUMN issue_key TEXT")
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_events_chain_position "
            "ON audit_events(chain_position)"
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_events_operation_key "
            "ON audit_events(operation_key)"
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_alerts_unresolved_issue "
            "ON alerts(issue_key) WHERE resolved = 0 AND issue_key IS NOT NULL"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_validation_results_run_id "
            "ON validation_results(record_id, run_id)"
        )


def create_land_record(database_path: DatabasePath, fields: Mapping[str, Any]) -> Row:
    """Insert a record and return it. An ID is generated unless `id` is supplied."""
    _require_mapping(fields, "record fields")
    record_id = fields.get("id", _new_id())
    if not isinstance(record_id, str) or not record_id.strip() or len(record_id) > 64:
        raise ValueError("id must be a non-empty string of at most 64 characters")
    values = dict(fields)
    values.pop("id", None)
    if values.get("status", "pending") != "pending":
        raise ValueError("New records must start as pending review")
    values.pop("status", None)
    validated = _validate_record_fields(values, partial=False)
    timestamp = _now()
    columns = ["id", *validated, "created_at", "updated_at"]
    parameters = [record_id, *validated.values(), timestamp, timestamp]
    placeholders = ", ".join("?" for _ in columns)
    with _connect(database_path) as connection:
        with connection:
            connection.execute(
                f"INSERT INTO land_records ({', '.join(columns)}) VALUES ({placeholders})",
                parameters,
            )
            row = connection.execute(
                "SELECT * FROM land_records WHERE id = ?", (record_id,)
            ).fetchone()
            from modules.audit import append_event_to_connection

            append_event_to_connection(
                connection,
                record_id=record_id,
                action="record_created",
                actor="system",
                payload={
                    "record_id": record_id,
                    "fields": validated,
                },
                operation_key=f"record-created:{record_id}",
            )
    result = _as_dict(row)
    assert result is not None
    return result


def get_land_record(database_path: DatabasePath, record_id: str) -> Row | None:
    with _connect(database_path) as connection:
        row = connection.execute(
            "SELECT * FROM land_records WHERE id = ?", (record_id,)
        ).fetchone()
    return _as_dict(row)


def list_land_records(database_path: DatabasePath, *, limit: int = 100, offset: int = 0) -> list[Row]:
    _validate_pagination(limit, offset)
    with _connect(database_path) as connection:
        rows = connection.execute(
            "SELECT * FROM land_records ORDER BY created_at, id LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [dict(row) for row in rows]


def update_land_record(
    database_path: DatabasePath, record_id: str, changes: Mapping[str, Any]
) -> Row | None:
    if "status" in changes:
        raise ValueError("Record status is managed by the review workflow")
    validated = _validate_record_fields(changes, partial=True)
    if not validated:
        raise ValueError("At least one land-record field must be supplied")
    assignments = ", ".join(f"{name} = ?" for name in validated)
    with _connect(database_path) as connection:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                "SELECT * FROM land_records WHERE id = ?", (record_id,)
            ).fetchone()
            if previous is None:
                return None
            changed = {
                key: value
                for key, value in validated.items()
                if previous[key] != value
            }
            if not changed:
                return dict(previous)
            next_status = (
                "flagged"
                if connection.execute(
                    """SELECT 1 FROM alerts
                       WHERE record_id = ? AND resolved = 0
                         AND severity IN ('high', 'critical') LIMIT 1""",
                    (record_id,),
                ).fetchone()
                is not None
                else "pending"
            )
            changed["status"] = next_status
            changed["human_reviewed"] = False
            cursor = connection.execute(
                f"UPDATE land_records SET {assignments}, status = ?, human_reviewed = 0, "
                "reviewed_at = NULL, record_revision = record_revision + 1, "
                "updated_at = ? WHERE id = ?",
                [*validated.values(), next_status, _now(), record_id],
            )
            if cursor.rowcount == 0:
                return None
            from modules.audit import append_event_to_connection

            status_changed = previous["status"] != next_status
            append_event_to_connection(
                connection,
                record_id=record_id,
                action="status_changed" if status_changed else "record_edited",
                actor="system",
                payload={
                    "record_id": record_id,
                    "changes": changed,
                    **(
                        {
                            "old_status": previous["status"],
                            "new_status": changed["status"],
                        }
                        if status_changed
                        else {}
                    ),
                },
            )
            row = connection.execute(
                "SELECT * FROM land_records WHERE id = ?", (record_id,)
            ).fetchone()
    return _as_dict(row)


def delete_land_record(database_path: DatabasePath, record_id: str) -> bool:
    with _connect(database_path) as connection:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            audit_exists = connection.execute(
                "SELECT 1 FROM audit_events WHERE record_id = ? LIMIT 1",
                (record_id,),
            ).fetchone()
            if audit_exists is not None:
                return False
            cursor = connection.execute(
                "DELETE FROM land_records WHERE id = ?", (record_id,)
            )
    return cursor.rowcount > 0


def create_validation_result(database_path: DatabasePath, fields: Mapping[str, Any]) -> Row:
    _require_mapping(fields, "validation result")
    if set(fields) - (_VALIDATION_FIELDS | {"run_id"}) or not _VALIDATION_FIELDS <= set(fields):
        raise ValueError(
            f"Validation result requires {', '.join(sorted(_VALIDATION_FIELDS))} "
            "and optional run_id"
        )
    record_id = _validate_text("record_id", fields["record_id"], 64)
    rule_code = _validate_text("rule_code", fields["rule_code"], 100)
    severity = _validate_severity(fields["severity"])
    if not isinstance(fields["passed"], bool):
        raise TypeError("passed must be a bool")
    message = _validate_text("message", fields["message"], 2000)
    run_id = fields.get("run_id")
    if run_id is not None:
        run_id = _validate_text("run_id", run_id, 64)
    result_id, created_at = _new_id(), _now()
    with _connect(database_path) as connection:
        with connection:
            connection.execute(
                """INSERT INTO validation_results
                   (id, record_id, rule_code, severity, passed, message, created_at, run_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    result_id, record_id, rule_code, severity, int(fields["passed"]),
                    message, created_at, run_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM validation_results WHERE id = ?", (result_id,)
            ).fetchone()
    result = _as_dict(row)
    assert result is not None
    result["passed"] = bool(result["passed"])
    return result


def list_validation_results(database_path: DatabasePath, record_id: str) -> list[Row]:
    with _connect(database_path) as connection:
        rows = connection.execute(
            "SELECT * FROM validation_results WHERE record_id = ? ORDER BY created_at, id",
            (record_id,),
        ).fetchall()
    results = [dict(row) for row in rows]
    for result in results:
        result["passed"] = bool(result["passed"])
    return results


def persist_validation_run(
    database_path: DatabasePath,
    record_id: str,
    run_id: str,
    findings: list[Mapping[str, Any]],
    alert_specs: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Persist findings, deduplicated alerts, status, and audit atomically."""
    record_id = _validate_text("record_id", record_id, 64)
    run_id = _validate_text("run_id", run_id, 64)
    with _connect(database_path) as connection:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            record = connection.execute(
                "SELECT * FROM land_records WHERE id = ?", (record_id,)
            ).fetchone()
            if record is None:
                raise ValueError(f"Land record {record_id!r} does not exist")

            for finding in findings:
                required = {"rule_code", "severity", "passed", "message"}
                if not required <= set(finding):
                    raise ValueError("Each validation finding is missing required fields")
                create_fields = {
                    "record_id": record_id,
                    "rule_code": finding["rule_code"],
                    "severity": finding["severity"],
                    "passed": finding["passed"],
                    "message": finding["message"],
                    "run_id": run_id,
                }
                _validate_text("rule_code", create_fields["rule_code"], 100)
                _validate_severity(create_fields["severity"])
                if not isinstance(create_fields["passed"], bool):
                    raise TypeError("validation finding passed must be a bool")
                _validate_text("message", create_fields["message"], 2000)
                connection.execute(
                    """INSERT INTO validation_results
                       (id, record_id, rule_code, severity, passed, message, created_at, run_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        _new_id(), record_id, create_fields["rule_code"],
                        create_fields["severity"], int(create_fields["passed"]),
                        create_fields["message"], _now(), run_id,
                    ),
                )

            created_alerts: list[Row] = []
            for spec in alert_specs:
                category = _validate_text("category", spec["category"], 100)
                severity = _validate_severity(spec["severity"])
                message = _validate_text("message", spec["message"], 2000)
                suggested_action = _validate_text(
                    "suggested_action", spec["suggested_action"], 1000
                )
                issue_key = _validate_text("issue_key", spec["issue_key"], 200)
                existing = connection.execute(
                    """SELECT * FROM alerts
                       WHERE issue_key = ? AND resolved = 0 ORDER BY created_at, id LIMIT 1""",
                    (issue_key,),
                ).fetchone()
                if existing is not None:
                    created_alerts.append(dict(existing))
                    continue
                alert_id = _new_id()
                connection.execute(
                    """INSERT INTO alerts
                       (id, record_id, category, severity, message, resolved, created_at,
                        suggested_action, issue_key)
                       VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)""",
                    (
                        alert_id, record_id, category, severity, message, _now(),
                        suggested_action, issue_key,
                    ),
                )
                created_alerts.append(
                    dict(
                        connection.execute(
                            "SELECT * FROM alerts WHERE id = ?", (alert_id,)
                        ).fetchone()
                    )
                )

            from modules.audit import _verify_connection, append_event_to_connection

            chain_status = _verify_connection(connection)
            audit_logged = False
            if chain_status["valid"]:
                append_event_to_connection(
                    connection,
                    record_id=record_id,
                    action="validation_run",
                    actor="validation_engine",
                    operation_key=f"validation-run:{record_id}:{run_id}",
                    payload={
                        "run_id": run_id,
                        "finding_count": len(findings),
                        "alerts_considered": len(alert_specs),
                    },
                )
                audit_logged = True

            next_status = _derive_record_status(
                connection, record_id, latest_findings=list(findings)
            )
            previous_status = record["status"]
            validation_timestamp = _now()
            connection.execute(
                """UPDATE land_records
                   SET status = ?, updated_at = ?, validation_record_revision = ?
                   WHERE id = ?""",
                (next_status, validation_timestamp, record["record_revision"], record_id),
            )
            if previous_status != next_status and audit_logged:
                append_event_to_connection(
                    connection,
                    record_id=record_id,
                    action="status_changed",
                    actor="review_workflow",
                    payload={
                        "old_status": previous_status,
                        "new_status": next_status,
                        "trigger": "validation_run",
                        "run_id": run_id,
                    },
                )
    for alert in created_alerts:
        alert["resolved"] = bool(alert["resolved"])
    return {
        "run_id": run_id,
        "status": next_status,
        "alerts": created_alerts,
        "audit_logged": audit_logged,
        "audit_valid": chain_status["valid"],
    }


def set_human_reviewed(database_path: DatabasePath, record_id: str) -> Row:
    """Complete human review only after checks and open issues are cleared."""
    record_id = _validate_text("record_id", record_id, 64)
    with _connect(database_path) as connection:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            record = connection.execute(
                "SELECT * FROM land_records WHERE id = ?", (record_id,)
            ).fetchone()
            if record is None:
                raise ValueError(f"Land record {record_id!r} does not exist")
            if record["human_reviewed"]:
                return dict(record)
            latest = connection.execute(
                """SELECT run_id FROM validation_results
                   WHERE record_id = ? ORDER BY rowid DESC LIMIT 1""",
                (record_id,),
            ).fetchone()
            if latest is None:
                raise ValueError("Run validation before completing human review")
            if (
                record["validation_record_revision"] is None
                or record["validation_record_revision"] != record["record_revision"]
            ):
                raise ValueError("Re-run validation after the record was last changed")
            run_id = latest["run_id"]
            if run_id is None:
                raise ValueError("Re-run validation to create a reviewable validation run")
            findings = connection.execute(
                """SELECT rule_code, severity, passed FROM validation_results
                   WHERE record_id = ? AND run_id = ?""",
                (record_id, run_id),
            ).fetchall()
            by_code = {row["rule_code"]: row for row in findings}
            required_codes = {
                "REQUIRED_SURVEY_NUMBER",
                "REQUIRED_OWNER_NAME",
                "REQUIRED_DISTRICT",
                "REQUIRED_VILLAGE",
                "REQUIRED_LAND_USE",
                "REQUIRED_AREA",
                "AREA_POSITIVE",
                "DOCUMENT_YEAR_RANGE",
            }
            missing = required_codes - set(by_code)
            if missing:
                raise ValueError("The latest validation run is incomplete")
            required_failures = sorted(
                code for code in required_codes if not bool(by_code[code]["passed"])
            )
            land_use_result = by_code.get("LAND_USE_ALLOWED") or by_code.get("LAND_USE_UNKNOWN")
            if land_use_result is None or not bool(land_use_result["passed"]):
                required_failures.append("LAND_USE_ALLOWED")
            if required_failures:
                raise ValueError(
                    "Required checks have blocking failures: "
                    + ", ".join(required_failures)
                )
            unresolved = connection.execute(
                """SELECT COUNT(*) FROM alerts
                   WHERE record_id = ? AND resolved = 0
                     AND severity IN ('high', 'critical')""",
                (record_id,),
            ).fetchone()[0]
            if unresolved:
                raise ValueError(
                    f"Resolve the {unresolved} open alert(s) before completing human review"
                )

            from modules.audit import append_event_to_connection

            append_event_to_connection(
                connection,
                record_id=record_id,
                action="human_review_completed",
                actor="reviewer",
                payload={"record_id": record_id, "validation_run_id": run_id},
            )
            next_status = _derive_record_status(
                connection, record_id, latest_findings=[
                    {
                        "severity": row["severity"],
                        "passed": bool(row["passed"]),
                    }
                    for row in findings
                ], human_reviewed=True,
            )
            connection.execute(
                """UPDATE land_records
                   SET human_reviewed = 1, reviewed_at = ?, status = ?, updated_at = ?
                   WHERE id = ?""",
                (_now(), next_status, _now(), record_id),
            )
            if record["status"] != next_status:
                append_event_to_connection(
                    connection,
                    record_id=record_id,
                    action="status_changed",
                    actor="review_workflow",
                    payload={
                        "old_status": record["status"],
                        "new_status": next_status,
                        "trigger": "human_review_completed",
                    },
                )
            updated = connection.execute(
                "SELECT * FROM land_records WHERE id = ?", (record_id,)
            ).fetchone()
    result = _as_dict(updated)
    assert result is not None
    return result


def create_audit_event(database_path: DatabasePath, fields: Mapping[str, Any]) -> Row:
    _require_mapping(fields, "audit event")
    if set(fields) - _AUDIT_FIELDS or not {"record_id", "action", "payload"} <= set(fields):
        raise ValueError("Audit event requires record_id, action, payload, and only supported optional fields")
    record_id = _validate_text("record_id", fields["record_id"], 64)
    action = _validate_text("action", fields["action"], 100)
    payload = fields["payload"]
    if not isinstance(payload, Mapping):
        raise TypeError("payload must be a mapping")
    if fields.get("previous_hash") is not None or fields.get("event_hash") is not None:
        raise ValueError("previous_hash and event_hash are generated by the audit chain")
    from modules.audit import append_event

    event = append_event(
        database_path,
        record_id=record_id,
        action=action,
        actor="system",
        payload=payload,
        timestamp=fields.get("event_timestamp"),
    )
    with _connect(database_path) as connection:
        row = connection.execute(
            "SELECT * FROM audit_events WHERE id = ?", (event["event_id"],)
        ).fetchone()
    result = _as_dict(row)
    assert result is not None
    return result


def list_audit_events(database_path: DatabasePath, record_id: str) -> list[Row]:
    with _connect(database_path) as connection:
        rows = connection.execute(
            "SELECT * FROM audit_events WHERE record_id = ? ORDER BY chain_position",
            (record_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def create_alert(database_path: DatabasePath, fields: Mapping[str, Any]) -> Row:
    _require_mapping(fields, "alert")
    optional = {"issue_key", "suggested_action"}
    if set(fields) - (_ALERT_FIELDS | optional) or not _ALERT_FIELDS <= set(fields):
        raise ValueError(
            f"Alert requires {', '.join(sorted(_ALERT_FIELDS))} and may include "
            "issue_key and suggested_action"
        )
    record_id = _validate_text("record_id", fields["record_id"], 64)
    category = _validate_text("category", fields["category"], 100)
    severity = _validate_severity(fields["severity"])
    message = _validate_text("message", fields["message"], 2000)
    suggested_action = _validate_text(
        "suggested_action",
        fields.get("suggested_action", "Review the record and source document."),
        1000,
    )
    issue_key = fields.get("issue_key")
    if issue_key is not None:
        issue_key = _validate_text("issue_key", issue_key, 200)
    with _connect(database_path) as connection:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            if issue_key is not None:
                existing = connection.execute(
                    "SELECT * FROM alerts WHERE issue_key = ? AND resolved = 0 LIMIT 1",
                    (issue_key,),
                ).fetchone()
                if existing is not None:
                    result = dict(existing)
                    result["resolved"] = bool(result["resolved"])
                    return result
            alert_id, created_at = _new_id(), _now()
            connection.execute(
                """INSERT INTO alerts
                   (id, record_id, category, severity, message, resolved, created_at,
                    suggested_action, issue_key)
                   VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)""",
                (
                    alert_id, record_id, category, severity, message, created_at,
                    suggested_action, issue_key,
                ),
            )
            previous_status = connection.execute(
                "SELECT status FROM land_records WHERE id = ?", (record_id,)
            ).fetchone()["status"]
            next_status = _derive_record_status(connection, record_id)
            status_timestamp = _now()
            connection.execute(
                "UPDATE land_records SET status = ?, updated_at = ? WHERE id = ?",
                (next_status, status_timestamp, record_id),
            )
            from modules.audit import _verify_connection, append_event_to_connection

            chain_status = _verify_connection(connection)
            if not chain_status["valid"]:
                invalid = chain_status["first_invalid_event"]
                position = invalid["chain_position"] if invalid is not None else "chain head"
                raise ValueError(
                    f"Cannot create an alert while the audit chain is invalid at {position}: "
                    f"{chain_status['reason']}"
                )
            append_event_to_connection(
                connection,
                record_id=record_id,
                action="alert_created",
                actor="review_workflow",
                payload={
                    "alert_id": alert_id,
                    "category": category,
                    "severity": severity,
                    "issue_key": issue_key,
                },
            )
            if previous_status != next_status:
                append_event_to_connection(
                    connection,
                    record_id=record_id,
                    action="status_changed",
                    actor="review_workflow",
                    payload={
                        "old_status": previous_status,
                        "new_status": next_status,
                        "trigger": "alert_created",
                    },
                )
            row = connection.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
    result = _as_dict(row)
    assert result is not None
    result["resolved"] = bool(result["resolved"])
    return result


def list_alerts(
    database_path: DatabasePath, record_id: str, *, include_resolved: bool = True
) -> list[Row]:
    if not isinstance(include_resolved, bool):
        raise TypeError("include_resolved must be a bool")
    query = "SELECT * FROM alerts WHERE record_id = ?"
    parameters: tuple[Any, ...] = (record_id,)
    if not include_resolved:
        query += " AND resolved = 0"
    query += " ORDER BY created_at, id"
    with _connect(database_path) as connection:
        rows = connection.execute(query, parameters).fetchall()
    results = [dict(row) for row in rows]
    for result in results:
        result["resolved"] = bool(result["resolved"])
    return results


def list_all_alerts(
    database_path: DatabasePath, *, limit: int = 1000
) -> list[Row]:
    _validate_pagination(limit, 0)
    with _connect(database_path) as connection:
        rows = connection.execute(
            "SELECT * FROM alerts ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    results = [dict(row) for row in rows]
    for result in results:
        result["resolved"] = bool(result["resolved"])
    return results


def resolve_alert(database_path: DatabasePath, alert_id: str) -> Row | None:
    with _connect(database_path) as connection:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            alert = connection.execute(
                "SELECT * FROM alerts WHERE id = ?", (alert_id,)
            ).fetchone()
            if alert is None:
                return None
            if alert["resolved"]:
                result = dict(alert)
                result["resolved"] = True
                return result
            cursor = connection.execute(
                "UPDATE alerts SET resolved = 1 WHERE id = ? AND resolved = 0",
                (alert_id,),
            )
            if cursor.rowcount == 0:
                return None
            from modules.audit import append_event_to_connection

            append_event_to_connection(
                connection,
                record_id=alert["record_id"],
                action="alert_resolved",
                actor="reviewer",
                payload={
                    "alert_id": alert_id,
                    "category": alert["category"],
                    "issue_key": alert["issue_key"],
                },
            )
            previous = connection.execute(
                "SELECT status, human_reviewed FROM land_records WHERE id = ?",
                (alert["record_id"],),
            ).fetchone()
            next_status = _derive_record_status(connection, alert["record_id"])
            connection.execute(
                "UPDATE land_records SET status = ?, updated_at = ? WHERE id = ?",
                (next_status, _now(), alert["record_id"]),
            )
            if previous["status"] != next_status:
                append_event_to_connection(
                    connection,
                    record_id=alert["record_id"],
                    action="status_changed",
                    actor="review_workflow",
                    payload={
                        "old_status": previous["status"],
                        "new_status": next_status,
                        "trigger": "alert_resolved",
                    },
                )
            row = connection.execute(
                "SELECT * FROM alerts WHERE id = ?", (alert_id,)
            ).fetchone()
    result = _as_dict(row)
    if result is not None:
        result["resolved"] = bool(result["resolved"])
    return result


def _validate_pagination(limit: int, offset: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError("limit must be an integer between 1 and 1000")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a non-negative integer")
