"""SQLite persistence for land records and their related results/events/alerts.

All database functions take an explicit path and open their own connection.
Foreign-key enforcement is enabled for every connection. Timestamps are stored
as UTC ISO 8601 strings.
"""

from __future__ import annotations

import json
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
);

CREATE TABLE IF NOT EXISTS validation_results (
    id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL REFERENCES land_records(id) ON DELETE CASCADE,
    rule_code TEXT NOT NULL CHECK (length(trim(rule_code)) BETWEEN 1 AND 100),
    severity TEXT NOT NULL CHECK (severity IN ('info', 'low', 'medium', 'high', 'critical')),
    passed INTEGER NOT NULL CHECK (passed IN (0, 1)),
    message TEXT NOT NULL CHECK (length(trim(message)) BETWEEN 1 AND 2000),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL REFERENCES land_records(id) ON DELETE CASCADE,
    action TEXT NOT NULL CHECK (length(trim(action)) BETWEEN 1 AND 100),
    event_timestamp TEXT NOT NULL,
    previous_hash TEXT,
    event_hash TEXT,
    canonical_payload TEXT NOT NULL CHECK (json_valid(canonical_payload)),
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
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_validation_results_record_id
    ON validation_results(record_id);
CREATE INDEX IF NOT EXISTS idx_audit_events_record_id
    ON audit_events(record_id);
CREATE INDEX IF NOT EXISTS idx_alerts_record_id
    ON alerts(record_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalize_timestamp(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError("event_timestamp must be an ISO 8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("event_timestamp must be an ISO 8601 string with a timezone") from error
    if parsed.tzinfo is None:
        raise ValueError("event_timestamp must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


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


def _validate_severity(value: Any) -> str:
    severity = _validate_text("severity", value, 20)
    if severity not in _SEVERITIES:
        raise ValueError(f"severity must be one of {', '.join(sorted(_SEVERITIES))}")
    return severity


def initialize_database(database_path: DatabasePath) -> None:
    """Create the schema if absent; safe to call repeatedly on an existing DB."""
    with _connect(database_path) as connection:
        connection.executescript(_SCHEMA)


def create_land_record(database_path: DatabasePath, fields: Mapping[str, Any]) -> Row:
    """Insert a record and return it. An ID is generated unless `id` is supplied."""
    _require_mapping(fields, "record fields")
    record_id = fields.get("id", _new_id())
    if not isinstance(record_id, str) or not record_id.strip() or len(record_id) > 64:
        raise ValueError("id must be a non-empty string of at most 64 characters")
    values = dict(fields)
    values.pop("id", None)
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
    validated = _validate_record_fields(changes, partial=True)
    if not validated:
        raise ValueError("At least one land-record field must be supplied")
    assignments = ", ".join(f"{name} = ?" for name in validated)
    parameters = [*validated.values(), _now(), record_id]
    with _connect(database_path) as connection:
        with connection:
            cursor = connection.execute(
                f"UPDATE land_records SET {assignments}, updated_at = ? WHERE id = ?",
                parameters,
            )
            if cursor.rowcount == 0:
                return None
            row = connection.execute(
                "SELECT * FROM land_records WHERE id = ?", (record_id,)
            ).fetchone()
    return _as_dict(row)


def delete_land_record(database_path: DatabasePath, record_id: str) -> bool:
    with _connect(database_path) as connection:
        with connection:
            cursor = connection.execute(
                "DELETE FROM land_records WHERE id = ?", (record_id,)
            )
    return cursor.rowcount > 0


def create_validation_result(database_path: DatabasePath, fields: Mapping[str, Any]) -> Row:
    _require_mapping(fields, "validation result")
    if set(fields) != _VALIDATION_FIELDS:
        raise ValueError(f"Validation result fields must be exactly: {', '.join(sorted(_VALIDATION_FIELDS))}")
    record_id = _validate_text("record_id", fields["record_id"], 64)
    rule_code = _validate_text("rule_code", fields["rule_code"], 100)
    severity = _validate_severity(fields["severity"])
    if not isinstance(fields["passed"], bool):
        raise TypeError("passed must be a bool")
    message = _validate_text("message", fields["message"], 2000)
    result_id, created_at = _new_id(), _now()
    with _connect(database_path) as connection:
        with connection:
            connection.execute(
                """INSERT INTO validation_results
                   (id, record_id, rule_code, severity, passed, message, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (result_id, record_id, rule_code, severity, int(fields["passed"]), message, created_at),
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


def create_audit_event(database_path: DatabasePath, fields: Mapping[str, Any]) -> Row:
    _require_mapping(fields, "audit event")
    if set(fields) - _AUDIT_FIELDS or not {"record_id", "action", "payload"} <= set(fields):
        raise ValueError("Audit event requires record_id, action, payload, and only supported optional fields")
    record_id = _validate_text("record_id", fields["record_id"], 64)
    action = _validate_text("action", fields["action"], 100)
    payload = fields["payload"]
    if not isinstance(payload, Mapping):
        raise TypeError("payload must be a mapping")
    canonical_payload = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    timestamp = _normalize_timestamp(fields.get("event_timestamp", _now()))
    previous_hash, event_hash = fields.get("previous_hash"), fields.get("event_hash")
    for name, value in (("previous_hash", previous_hash), ("event_hash", event_hash)):
        if value is not None and (
            not isinstance(value, str)
            or len(value) != 64
            or any(char not in "0123456789abcdefABCDEF" for char in value)
        ):
            raise ValueError(f"{name} must be a 64-character SHA-256 hex digest or None")
    event_id = _new_id()
    with _connect(database_path) as connection:
        with connection:
            connection.execute(
                """INSERT INTO audit_events
                   (id, record_id, action, event_timestamp, previous_hash, event_hash, canonical_payload)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (event_id, record_id, action, timestamp, previous_hash, event_hash, canonical_payload),
            )
            row = connection.execute(
                "SELECT * FROM audit_events WHERE id = ?", (event_id,)
            ).fetchone()
    result = _as_dict(row)
    assert result is not None
    return result


def list_audit_events(database_path: DatabasePath, record_id: str) -> list[Row]:
    with _connect(database_path) as connection:
        rows = connection.execute(
            "SELECT * FROM audit_events WHERE record_id = ? ORDER BY event_timestamp, id",
            (record_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def create_alert(database_path: DatabasePath, fields: Mapping[str, Any]) -> Row:
    _require_mapping(fields, "alert")
    if set(fields) != _ALERT_FIELDS:
        raise ValueError(f"Alert fields must be exactly: {', '.join(sorted(_ALERT_FIELDS))}")
    record_id = _validate_text("record_id", fields["record_id"], 64)
    category = _validate_text("category", fields["category"], 100)
    severity = _validate_severity(fields["severity"])
    message = _validate_text("message", fields["message"], 2000)
    alert_id, created_at = _new_id(), _now()
    with _connect(database_path) as connection:
        with connection:
            connection.execute(
                """INSERT INTO alerts (id, record_id, category, severity, message, resolved, created_at)
                   VALUES (?, ?, ?, ?, ?, 0, ?)""",
                (alert_id, record_id, category, severity, message, created_at),
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


def resolve_alert(database_path: DatabasePath, alert_id: str) -> Row | None:
    with _connect(database_path) as connection:
        with connection:
            cursor = connection.execute(
                "UPDATE alerts SET resolved = 1 WHERE id = ?", (alert_id,)
            )
            if cursor.rowcount == 0:
                return None
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
