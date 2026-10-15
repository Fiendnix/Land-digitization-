"""Tamper-evident SHA-256 hash-chained audit log.

The chain is global to one SQLite database and ordered by ``chain_position``.
It is not a production blockchain and does not protect against an attacker
able to rewrite the complete database and recompute every hash.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GENESIS_HASH = "0" * 64
_HASH_FIELDS = (
    "chain_position",
    "event_id",
    "record_id",
    "action",
    "timestamp",
    "canonical_payload",
    "previous_hash",
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _normalize_timestamp(value: str | None) -> str:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("timestamp must be a valid ISO 8601 value") from error
        if parsed.tzinfo is None:
            raise ValueError("timestamp must include a timezone")
        parsed = parsed.astimezone(timezone.utc)
    else:
        raise TypeError("timestamp must be a string or None")
    return parsed.isoformat(timespec="microseconds")


def _hash_event(event: Mapping[str, Any]) -> str:
    hashed_data = {key: event[key] for key in _HASH_FIELDS}
    canonical = _canonical_json(hashed_data).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _event_from_row(row: sqlite3.Row) -> dict[str, Any]:
    try:
        payload_envelope = json.loads(row["canonical_payload"])
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        payload_envelope = {}
        payload_error = str(error)
    else:
        payload_error = None
    if (
        not isinstance(payload_envelope, dict)
        or "payload" not in payload_envelope
        or not isinstance(payload_envelope.get("actor"), str)
    ):
        payload_error = payload_error or "canonical payload must contain actor and payload"
    return {
        "chain_position": row["chain_position"],
        "event_id": row["id"],
        "record_id": row["record_id"],
        "action": row["action"],
        "timestamp": row["event_timestamp"],
        "actor": payload_envelope.get("actor"),
        "payload": payload_envelope.get("payload"),
        "canonical_payload": row["canonical_payload"],
        "previous_hash": row["previous_hash"],
        "event_hash": row["event_hash"],
        "operation_key": row["operation_key"],
        "payload_error": payload_error,
    }


def _row_by_operation_key(
    connection: sqlite3.Connection, operation_key: str | None
) -> dict[str, Any] | None:
    if operation_key is None:
        return None
    row = connection.execute(
        "SELECT * FROM audit_events WHERE operation_key = ?", (operation_key,)
    ).fetchone()
    return _event_from_row(row) if row is not None else None


def append_event_to_connection(
    connection: sqlite3.Connection,
    *,
    record_id: str,
    action: str,
    payload: Mapping[str, Any],
    actor: str = "system",
    timestamp: str | None = None,
    operation_key: str | None = None,
) -> dict[str, Any]:
    """Append an event using a caller-owned active transaction."""
    for field, value in (("record_id", record_id), ("action", action), ("actor", actor)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")
    if not isinstance(payload, Mapping):
        raise TypeError("payload must be a mapping")
    if operation_key is not None and (
        not isinstance(operation_key, str) or not operation_key.strip()
    ):
        raise ValueError("operation_key must be a non-empty string or None")

    existing = _row_by_operation_key(connection, operation_key)
    if existing is not None:
        expected_payload = _canonical_json({"actor": actor.strip(), "payload": payload})
        if (
            existing["record_id"] != record_id.strip()
            or existing["action"] != action.strip()
            or existing["canonical_payload"] != expected_payload
        ):
            raise ValueError("operation_key was already used for a different audit event")
        return existing

    verification = _verify_connection(connection)
    if not verification["valid"]:
        invalid = verification["first_invalid_event"]
        position = invalid["chain_position"] if invalid is not None else "chain head"
        raise ValueError(
            f"Cannot append to an invalid audit chain at {position}: {verification['reason']}"
        )

    normalized_timestamp = _normalize_timestamp(timestamp)
    canonical_payload = _canonical_json({"actor": actor.strip(), "payload": payload})
    state = connection.execute(
        "SELECT last_position, last_hash FROM audit_chain_state WHERE singleton_id = 1"
    ).fetchone()
    if state is None:
        legacy_event = connection.execute(
            "SELECT 1 FROM audit_events LIMIT 1"
        ).fetchone()
        if legacy_event is not None:
            raise ValueError(
                "Cannot append to an audit log without chain state; "
                "verify and migrate that history explicitly."
            )
        chain_position = 1
        previous_hash = GENESIS_HASH
    else:
        chain_position = int(state["last_position"]) + 1
        previous_hash = str(state["last_hash"])

    event = {
        "chain_position": chain_position,
        "event_id": str(uuid.uuid4()),
        "record_id": record_id.strip(),
        "action": action.strip(),
        "timestamp": normalized_timestamp,
        "actor": actor.strip(),
        "payload": dict(payload),
        "canonical_payload": canonical_payload,
        "previous_hash": previous_hash,
    }
    event_hash = _hash_event(event)
    connection.execute(
        """INSERT INTO audit_events
           (id, record_id, action, event_timestamp, previous_hash, event_hash,
            canonical_payload, chain_position, operation_key)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event["event_id"],
            event["record_id"],
            event["action"],
            event["timestamp"],
            previous_hash,
            event_hash,
            canonical_payload,
            chain_position,
            operation_key,
        ),
    )
    connection.execute(
        """INSERT INTO audit_chain_state (singleton_id, last_position, last_hash)
           VALUES (1, ?, ?)
           ON CONFLICT(singleton_id) DO UPDATE SET
             last_position = excluded.last_position,
             last_hash = excluded.last_hash""",
        (chain_position, event_hash),
    )
    event["event_hash"] = event_hash
    event["operation_key"] = operation_key
    return event


def _connect(database_path: str | Path) -> sqlite3.Connection:
    path = str(database_path)
    if path != ":memory:":
        Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def append_event(
    database_path: str | Path,
    *,
    record_id: str,
    action: str,
    payload: Mapping[str, Any],
    actor: str = "system",
    timestamp: str | None = None,
    operation_key: str | None = None,
) -> dict[str, Any]:
    """Append one event with an immediate write transaction."""
    connection = _connect(database_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        event = append_event_to_connection(
            connection,
            record_id=record_id,
            action=action,
            payload=payload,
            actor=actor,
            timestamp=timestamp,
            operation_key=operation_key,
        )
        connection.commit()
        return event
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def list_events(database_path: str | Path) -> list[dict[str, Any]]:
    """Return the global event timeline in chain order."""
    connection = _connect(database_path)
    try:
        rows = connection.execute(
            "SELECT * FROM audit_events ORDER BY chain_position"
        ).fetchall()
    finally:
        connection.close()
    return [_event_from_row(row) for row in rows]


def _verify_connection(connection: sqlite3.Connection) -> dict[str, Any]:
    rows = connection.execute(
        "SELECT * FROM audit_events ORDER BY chain_position"
    ).fetchall()
    events = [_event_from_row(row) for row in rows]
    expected_previous = GENESIS_HASH
    expected_position = 1
    for event in events:
        position = event["chain_position"]
        if position != expected_position:
            return {
                "valid": False,
                "event_count": len(events),
                "first_invalid_event": event,
                "reason": (
                    f"Expected chain position {expected_position}, found {position}; "
                    "an event is missing or the timeline was reordered."
                ),
            }
        if event["payload_error"] is not None:
            return {
                "valid": False,
                "event_count": len(events),
                "first_invalid_event": event,
                "reason": f"Event payload is invalid: {event['payload_error']}",
            }
        if event["previous_hash"] != expected_previous:
            return {
                "valid": False,
                "event_count": len(events),
                "first_invalid_event": event,
                "reason": "previous_hash does not match the preceding event hash.",
            }
        try:
            canonical_payload = _canonical_json(
                {"actor": event["actor"], "payload": event["payload"]}
            )
            payload_is_canonical = canonical_payload == event["canonical_payload"]
            calculated_hash = _hash_event(event)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            return {
                "valid": False,
                "event_count": len(events),
                "first_invalid_event": event,
                "reason": f"Event payload is invalid: {error}",
            }
        if not payload_is_canonical:
            return {
                "valid": False,
                "event_count": len(events),
                "first_invalid_event": event,
                "reason": "canonical_payload is malformed or not deterministically serialized.",
            }
        if event["event_hash"] != calculated_hash:
            return {
                "valid": False,
                "event_count": len(events),
                "first_invalid_event": event,
                "reason": "event_hash does not match the event contents.",
            }
        expected_previous = event["event_hash"]
        expected_position += 1
    state = connection.execute(
        "SELECT last_position, last_hash FROM audit_chain_state WHERE singleton_id = 1"
    ).fetchone()
    state_matches = (
        state is None
        if not events
        else (
            state is not None
            and state["last_position"] == events[-1]["chain_position"]
            and state["last_hash"] == events[-1]["event_hash"]
        )
    )
    if not state_matches:
        return {
            "valid": False,
            "event_count": len(events),
            "first_invalid_event": events[-1] if events else None,
            "reason": "Stored events do not agree with the audit-chain head state; an event may be missing.",
        }
    return {
        "valid": True,
        "event_count": len(events),
        "first_invalid_event": None,
        "reason": "All stored audit events verify from the fixed genesis hash.",
    }


def verify_chain(database_path: str | Path) -> dict[str, Any]:
    """Verify every stored event and report the first invalid position."""
    connection = _connect(database_path)
    try:
        return _verify_connection(connection)
    finally:
        connection.close()
