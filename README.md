# Intelligent Land Record Digitalization and Validation System

**Smart India Hackathon 2026 · Problem statement SIH26018**

A Python-first Streamlit MVP that turns scanned land-record documents into
editable structured fields, persists reviewer-entered records in SQLite, and
demonstrates deterministic validation, explainable scoring, sample geometry
checks, alerts, and a tamper-evident audit history.

> **Important:** The software is a demonstration and review aid. OCR does not
> prove ownership. Scores are not legal conclusions or probabilities of
> ownership or fraud. Synthetic demo records, reference data, and geometries
> are fictional and not authoritative government information.

## MVP features

- Image and scanned-PDF upload, preview, image preprocessing, Tesseract OCR,
  raw OCR display, and deterministic editable field suggestions.
- SQLite storage with stable record IDs, field checks, persistent validation
  results and alerts, and reviewer-gated status transitions.
- Rule-based checks for required values, numeric area, year range, land use,
  duplicate identifiers, OCR confidence, and optional separate reference rows.
- An explainable trust-score breakdown with explicit missing-component
  handling and provisional score labeling.
- Demo-only map rendering and geometry checks for valid, overlapping, and
  invalid synthetic parcel shapes.
- SHA-256 hash-chained audit events and an integrity verification screen.
- An idempotent seed script, a generated sample scan, and an explicit,
  confirmation-based demo database reset.

## Architecture and data flow

The Streamlit shell in `app.py` calls focused modules for OCR, extraction,
review input, SQLite, validation, geospatial evaluation, score calculation,
and audit operations. The app does not expose arbitrary SQL to the UI. See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for module contracts, tables,
score details, and chain verification.

**Document path:** upload → decode/preview → Tesseract → raw text and
deterministic field suggestions → reviewer edits → input checks → SQLite
record and creation audit event.

**Review path:** select record → run value/duplicate/reference checks and,
in demo mode, sample GeoJSON checks → persist findings, deduplicated alerts,
and validation audit event → inspect weighted score → complete human review
only after blocking conditions are cleared → verify the audit history.

**Trust score:** `T = 0.30O + 0.30D + 0.25G + 0.15H`. The result shows all
components and weights. Missing components are never silently assigned a
perfect score or reweighted; any incomplete result is labeled provisional.
See the architecture document for the data-consistency rule and demo spatial
score policy.

## Technology stack

- Python **3.12+** (the project currently declares `requires-python >=3.12`)
- `uv` for local Python environment and dependency management; `pyproject.toml`
  and committed `uv.lock` are canonical
- Streamlit, pandas
- SQLite via Python's standard-library `sqlite3`
- Tesseract OCR through pytesseract; Pillow, OpenCV, PyMuPDF
- GeoPandas, Shapely, Folium, and streamlit-folium
- Python `hashlib` SHA-256
- Pytest

`requirements.txt` is a pinned export of runtime dependencies for platforms
that expect that format. Make dependency changes in `pyproject.toml` with
`uv add` or `uv add --dev`, regenerate `uv.lock`, then refresh the export as
described below. Pytest is a development dependency and is not included in
the runtime requirements.

After changing dependencies with uv, regenerate that deployment export with:

```sh
uv export --format requirements.txt --no-dev --no-emit-project --no-hashes > requirements.txt
```

## Prerequisites

- macOS, Linux, or Windows with Python 3.12 or newer available through `uv`.
- `uv` installed and available on `PATH`.
- Native Tesseract OCR installed separately for actual OCR operations. The
  app can start without Tesseract, but OCR will not run.
- For non-English OCR, install the corresponding Tesseract trained-data
  language files and set the desired language code in Digitize Record.
- No external government APIs or accounts are required. The Esri World Imagery
  satellite tiles displayed on the map require internet access; parcel validation
  remains local.

### Install Tesseract

Tesseract is a native operating-system executable, not a Python dependency.

**macOS (Homebrew):**

```sh
brew install tesseract
tesseract --version
```

**Ubuntu/Debian:**

```sh
sudo apt-get update
sudo apt-get install -y tesseract-ocr tesseract-ocr-eng
tesseract --version
```

**Windows:** install the Tesseract OCR distribution for Windows using its
official installer or an organization-approved package source. Add the
directory containing `tesseract.exe` to `PATH`, or set `TESSERACT_CMD` to the
full executable path. Restart the terminal or app after changing `PATH`.

If Tesseract is installed but not on `PATH`, configure its path:

```sh
TESSERACT_CMD=/absolute/path/to/tesseract uv run streamlit run app.py
```

On Windows, set `TESSERACT_CMD` in the process environment using the normal
PowerShell or Command Prompt syntax.

## Install and run locally

From the repository root:

```sh
uv sync --locked
uv run streamlit run app.py
```

The standard app uses `data/land_records.sqlite3`, created on startup. To run
Streamlit directly after synchronizing the uv-managed environment, activate
that environment (`source .venv/bin/activate` on macOS/Linux, or the matching
activation script on Windows), then use:

```sh
streamlit run app.py
```

The app binds to Streamlit's default local port. If that port is occupied:

```sh
uv run streamlit run app.py --server.port 8502
```

## Initialize the synthetic demo data

The demo database is separate from the ordinary local application database:

```sh
uv run python scripts/seed_demo.py
LAND_RECORDS_DB=data/demo_land_records.sqlite3 LAND_RECORDS_DEMO=1 uv run streamlit run app.py
```

The seed creates ten records using stable IDs and adds validation results,
alerts, audit history, fictional reference comparisons, and geometry outcomes.
Rerunning it against a complete seed inserts no duplicate records or events.
If only some stable demo IDs exist, seeding fails without overwriting data.

The script also generates
[`data/demo/sample-land-record.png`](data/demo/sample-land-record.png), a
fictional scanned-style sample. Upload it from Digitize Record, inspect the
OCR suggestions, edit a field, and save to demonstrate intake. Full sample
descriptions and explicit reset steps are in [data/demo/README.md](data/demo/README.md).

**Reset is destructive.** This affects only
`data/demo_land_records.sqlite3` and its SQLite WAL/SHM sidecars; it does not
touch `data/land_records.sqlite3`. The script asks for the exact phrase
`RESET DEMO`:

```sh
uv run python scripts/seed_demo.py --reset
```

Only use `--yes` when deliberately automating that deletion:

```sh
uv run python scripts/seed_demo.py --reset --yes
```

## Tests

```sh
uv run pytest
```

Tests use temporary databases and synthetic in-memory/file fixtures. They
cover SQLite persistence, validation, trust-score boundaries, OCR and
extraction behavior, demo seeding and spatial findings, workflow statuses,
alerts, and audit tampering.

## Troubleshooting

| Symptom | Resolution |
|---|---|
| `uv: command not found` | Install uv using the official uv installation instructions, then open a new terminal and check `uv --version`. |
| Dependency or lock mismatch | Run `uv sync --locked`; if project dependencies intentionally changed, update through `uv add`/`uv add --dev`, refresh the lock, and review the dependency diff. |
| Tesseract not found | Install the native executable, verify with `tesseract --version`, or set `TESSERACT_CMD` to its full path. |
| OCR cannot read a language | Install that language's Tesseract trained-data file and use the installed language code, for example `eng` or `eng+hin`. |
| Upload rejected | Supported inputs are PNG, JPG/JPEG, and scanned PDF, up to 20 MB. Re-export damaged or unsupported documents to one of those formats. |
| PDF preview/OCR fails | Confirm the PDF opens locally and contains scanned pages. Password-protected or malformed documents may be rejected. |
| Port is already in use | Start on another port, for example `uv run streamlit run app.py --server.port 8502`. |
| Demo data is only partially present | Do not remove individual rows. Inspect the isolated demo database, then use the confirmation-based reset only if its entire local demo history may be deleted. |
| Map is missing records | The map evaluates only the included fictional GeoJSON; non-demo records without a matching feature correctly show spatial validation as unavailable. |
| Changes appear stale | Use Streamlit's Rerun control or restart the app. SQLite data persists locally across app reruns. |

## Deployment readiness and limitations

This is a Python/Streamlit application; Vercel is not required. The repository
contains a runtime `requirements.txt` export and `packages.txt` for installing
Tesseract on a compatible Streamlit Community Cloud Linux image. Select a
Python 3.12 runtime when creating the app and configure the repository root
and `app.py` as its entry point.

**Deployment has not been performed or authorized.** Runtime compatibility
with a hosted Linux image is prepared but must still be validated by the
hosting build. `packages.txt` can install the native Tesseract executable
where the platform supports apt packages, but additional OCR languages may
need separate setup. GeoPandas/Shapely and their native geospatial wheel
compatibility must be checked in the target platform's build logs.

Do not treat a hosted instance's local filesystem or SQLite file as durable:
ephemeral hosts can discard database writes and uploaded documents on restart,
rebuild, or sleep. The app stores records in local SQLite; uploaded binaries
are not persisted. Do not upload a local SQLite database, private scans,
credentials, or other user documents to GitHub. A persistent production
deployment needs an explicitly selected durable database/document-storage
service and an authorization/security review. No platform data persistence is
promised here.

## Current limitations and future work

- Field extraction uses deterministic English-oriented rules; it needs broader
  multilingual/layout test coverage and manual verification.
- OCR confidence describes OCR engine output, not legal reliability. Extraction
  match estimates are heuristics.
- The demo's references and geometry are fictional and only for exercising the
  UI. There is no government registry connection or authoritative parcel map.
- The GIS scoring policy is a demonstration rule; it is not cadastral
  boundary validation, and planar overlap checks on the included longitude /
  latitude sample are not a surveying measurement.
- SQLite is appropriate for a local hackathon MVP, not concurrent production
  hosting. Authentication, authorization, durable backups, and a persistent
  deployment store are not implemented.
- The hash chain detects data changes relative to its stored head, but cannot
  stop an attacker who can rewrite the entire database and recompute the chain.
- No production blockchain, external LLM, or external government API is used.

Suggested next steps are to rehearse the
[manual demonstration checklist](docs/DEMO_CHECKLIST.md), test the hosted
Linux build without private data, and choose durable storage and access control
before any real-record use.
