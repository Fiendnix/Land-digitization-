from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _new_id() -> str:
    return str(uuid4())


@dataclass
class LandRecord:
    record_id: str = field(default_factory=_new_id)
    parcel_id: str | None = None
    survey_number: str | None = None
    owner_name: str | None = None
    village: str | None = None
    taluka: str | None = None
    district: str | None = None
    state: str | None = None
    area: float | None = None
    area_unit: str | None = None
    land_use: str | None = None
    document_path: str | None = None
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = field(default_factory=_utc_now_iso)


@dataclass
class ValidationResult:
    record_id: str
    validation_type: str
    status: Literal["passed", "failed", "needs_review"]
    validation_id: str = field(default_factory=_new_id)
    details: dict[str, object] | None = None
    created_at: str = field(default_factory=_utc_now_iso)


@dataclass
class Alert:
    record_id: str
    severity: Literal["low", "medium", "high", "critical"]
    message: str
    alert_id: str = field(default_factory=_new_id)
    is_resolved: bool = False
    created_at: str = field(default_factory=_utc_now_iso)


@dataclass
class AuditEvent:
    event_type: str
    record_id: str | None = None
    actor_id: str | None = None
    event_id: str = field(default_factory=_new_id)
    details: dict[str, object] | None = None
    created_at: str = field(default_factory=_utc_now_iso)
