# Intelligent Land Record Digitalization and Validation System

## Database design

The MVP database uses SQLite. All identifiers are stable UUID strings; child
rows reference `land_records.id` and are removed when their parent is deleted.
Connections enable SQLite foreign-key enforcement. Generated timestamps are
UTC ISO 8601 strings; supplied audit-event timestamps are normalized to UTC.

| Table | Fields and constraints |
|---|---|
| `land_records` | `id` text primary key; required `parcel_id` (1–100 chars), `survey_number` (1–100), `owner_name` (1–200), `district` and `village` (1–120 each), `land_use` (1–80); `area` is positive and at most 1e12; `document_year` is 1000–9999; optional `ocr_confidence` and `trust_score` are 0–100; `status` is `pending`, `validated`, or `flagged`; optional source-document SHA-256 hex digest; `created_at` and `updated_at`. |
| `validation_results` | UUID `id`; `record_id` foreign key; `rule_code` (1–100 chars); `severity` (`info`, `low`, `medium`, `high`, `critical`); boolean `passed`; `message` (1–2000 chars); `created_at`. |
| `audit_events` | UUID `id`; `record_id` foreign key; `action` (1–100 chars); UTC ISO 8601 `event_timestamp`; optional SHA-256 `previous_hash` and `event_hash`; valid JSON `canonical_payload`. This layer stores supplied hashes/payloads but does not generate or verify an audit chain. |
| `alerts` | UUID `id`; `record_id` foreign key; `category` (1–100 chars); `severity` (`info`, `low`, `medium`, `high`, `critical`); `message` (1–2000 chars); boolean `resolved` (default false); `created_at`. |

The relationships are one-to-many from `land_records` to `validation_results`,
`audit_events`, and `alerts`, with `ON DELETE CASCADE`. Document binary data is
not stored in the database.

## Database module

`modules.database.initialize_database(path)` creates the schema idempotently.
All functions take an explicit database path; records are returned as
dictionaries. The module exposes:

- Records: `create_land_record`, `get_land_record`, `list_land_records`,
  `update_land_record`, `delete_land_record`.
- Validation results: `create_validation_result`, `list_validation_results`.
- Audit events: `create_audit_event`, `list_audit_events`.
- Alerts: `create_alert`, `list_alerts`, `resolve_alert`.

Record create/update fields are mappings. IDs are generated when a new record
does not supply one. Record updates accept only the documented record fields.
No API accepts arbitrary SQL.

Run database tests with `uv run pytest`.

## Streamlit application shell

Start the interface from the project root with:

```bash
uv run streamlit run app.py
```

The local SQLite database is created at `data/land_records.sqlite3` when the
app starts. The Digitize Record and Geospatial Validation pages are placeholders;
OCR, GIS analysis, trust scoring, and audit-chain verification are not active.
