# Architecture

## Runtime and boundaries

`app.py` is the Streamlit presentation/router layer. It resolves the local
SQLite path from `LAND_RECORDS_DB`, enables the fictional-data notice with
`LAND_RECORDS_DEMO=1` (also inferred from the isolated demo DB filename), and
calls domain modules rather than issuing UI SQL. The default DB is
`data/land_records.sqlite3`; the demo DB is `data/demo_land_records.sqlite3`.

| Module | Responsibility |
|---|---|
| `modules/ocr.py` | Validate upload size/type, decode image or PDF pages, preprocess pixels, invoke native Tesseract through pytesseract, and return raw text and 0–100 OCR-engine confidence. |
| `modules/extraction.py` | Deterministically parse labeled fields and provide rule-match estimates; never fills missing values from guesses. |
| `modules/record_review.py` | Validate reviewer-entered fields and build a database-ready record payload. |
| `modules/database.py` | Initialize/migrate SQLite idempotently; parameterized CRUD; persist record, validation, alerts, review status, and audit-related writes with transactions. |
| `modules/validation.py` | Pure deterministic required/value/duplicate/reference checks. Does not query the UI or perform geometry checks. |
| `modules/geospatial.py` | Load the separate sample GeoJSON, validate polygon geometry and detect positive-area overlaps; returns unavailable when no matching geometry exists. |
| `modules/trust_score.py` | Apply fixed weighted score and return component inputs, contributions, missing components, category, and provisional/complete state. |
| `modules/review_workflow.py` | Orchestrate validation, add persisted findings/alerts, deduplicate open issues, calculate scores, manage review status, and aggregate dashboard metrics. |
| `modules/audit.py` | Append and verify the global per-database SHA-256 audit chain. |
| `scripts/seed_demo.py` | Deterministically seed fictional records and their validation/audit outcomes, generate the sample scan, and safely reset only the isolated demo DB after confirmation. |

## Data flow

1. Uploaded bytes are validated and decoded in memory. The original upload is
   SHA-256 hashed for identification only; the database stores the hash, not
   document bytes.
2. OCR output is displayed for inspection; deterministic extraction is a
   suggestion. Original extracted values are retained separately in Streamlit
   session state while form values remain editable.
3. On user save, field shape/limits are checked. SQLite creates a pending
   record and appends a `record_created` audit event in one transaction.
4. Validation receives the current record, other database records and an
   optional separate reference collection. In demo mode the app also supplies
   feature-matched sample geometry and findings.
5. Findings and actionable alerts are persisted together with a validation
   run, record status, and audit event. Repeated unresolved rule alerts are
   deduplicated. Reference or spatial unavailability is not represented as a
   successful match.
6. A reviewer explicitly completes human review only after current checks pass
   and blocking alerts are cleared. Audit history can be verified from the
   fixed genesis hash at any time.

## SQLite schema and relationships

Every SQLite connection enables foreign keys. Generated timestamps use
UTC ISO 8601 strings. All related rows use stable IDs and reference
`land_records.id`.

| Table | Purpose and key constraints |
|---|---|
| `land_records` | Stable text primary key; parcel/survey identifiers; owner, district, village, land use; positive numeric area and optional unit; document year; optional OCR confidence, trust score, source SHA-256; status, creation/update timestamps, reviewer marker, revision and validation revision. |
| `validation_results` | UUID primary key; record foreign key; rule code, severity, boolean passed, human-readable message, UTC timestamp, optional run ID. |
| `alerts` | UUID primary key; record foreign key; category, severity, message, resolved flag, UTC timestamp, suggested action, issue key. A partial unique index prevents duplicate unresolved issue keys. Resolved history remains stored. |
| `audit_events` | UUID primary key; record foreign key; action, UTC timestamp, canonical JSON payload, previous/event SHA-256 hashes, globally unique chain position and optional idempotency key. |
| `audit_chain_state` | Singleton row containing the latest global chain position and hash; verification compares this stored head to the events. |

`land_records` has one-to-many relations to validation results, alerts, and
audit events. The ordinary UI does not store document binary data or expose
arbitrary SQL.

## Trust score

The score uses fixed weights:

```text
T = 0.30O + 0.30D + 0.25G + 0.15H
```

- **O** — mean usable Tesseract OCR confidence, 0–100; unavailable stays
  missing.
- **D** — data consistency, initialized to 100 and reduced by 25 for each
  distinct major critical data inconsistency, floored at 0. Related required
  and value failures for area/land use are counted once. Warning-level
  duplicates do not count as major.
- **G** — in demo mode, valid/non-overlapping sample geometry = 100, overlapping
  valid polygon = 50, invalid geometry = 0. No matching feature is unavailable.
  Outside demo mode, geometry is unavailable unless explicitly supplied.
- **H** — 100 when a non-empty audit chain verifies, 0 when a non-empty chain
  fails, unavailable for an empty chain.

The fixed weights are never redistributed. The returned subtotal for missing
inputs is **provisional**, not a complete score and has no confidence category.
Complete categories are High confidence (80–100), Review recommended
(60–79.9), and High risk / manual review (below 60). It is a software
verification indicator, not a legal ownership probability, certificate, or
proof of fraud.

## Audit-chain verification

The chain is global to one SQLite database. Events are serialized using sorted
JSON keys, compact separators, UTF-8 and deterministic payload structure.
Each SHA-256 covers chain position, event ID, record ID, action, timestamp,
canonical payload (including actor), and previous hash. Event 1 links to the
fixed all-zero 64-character genesis hash; every following event links to the
preceding event hash.

Verification checks the genesis link, contiguous ordering, previous-hash links,
payload canonicalization, recomputed event hash, and final stored chain head.
It reports the first invalid event and never repairs history. This is
tamper-evident, not blockchain storage: an attacker able to rewrite the entire
database and recompute all event hashes and the chain head can defeat it.

## Deployment boundary

`requirements.txt` is a pinned runtime export generated from `uv.lock` without
the development group. `packages.txt` requests native Tesseract and English trained data on platforms
that support apt packages. The map page uses Esri World Imagery tiles, so it
requires internet access and sends tile requests to Esri. Native geospatial
wheels and Tesseract installation still require confirmation from the selected
hosting build. SQLite on an ephemeral host is not durable; production use
requires an authorized durable database/document-storage design and appropriate
access control.
