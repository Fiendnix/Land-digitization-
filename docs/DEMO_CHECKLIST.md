# SIH26018 manual demonstration checklist

## Before the presentation

- [ ] Use a machine with Python 3.12+, `uv`, and native Tesseract installed.
- [ ] In the project directory run `uv sync --locked`.
- [ ] Seed the isolated demonstration DB with `uv run python scripts/seed_demo.py`.
- [ ] Start the demo using the README's `LAND_RECORDS_DB=...` and
      `LAND_RECORDS_DEMO=1` launch command.
- [ ] Confirm the top-of-page notice says **DEMO MODE · ILLUSTRATIVE DATA ONLY**.
- [ ] Confirm the dashboard has ten records and the expected score categories.
- [ ] Confirm the sample scan download is available on Digitize Record.
- [ ] Optional: upload the PDFs in `data/demo/test_documents/` to test full extraction, missing owner/duplicate survey, and invalid area cases.
- [ ] Keep the regular `data/land_records.sqlite3` and any private records out of
      the presentation and repository.

## Guided path: dashboard → OCR → review → validation → score → map → audit

### 1. Dashboard

- [ ] Show total, pending review, flagged, and verified metrics.
- [ ] Show the high-confidence, review-recommended, and high-risk score
      category counts.
- [ ] Point out the fictional-data disclaimer and guided path.

### 2. Digitize and review

- [ ] Open **Digitize Record**.
- [ ] Download the synthetic scan `sample-land-record.png`.
- [ ] Upload it; inspect the side-by-side document preview.
- [ ] Run OCR and show raw recognized text plus OCR confidence.
- [ ] Expand the read-only original extraction snapshot.
- [ ] Change a field such as Owner name to `Avery Reviewed Demo`.
- [ ] Click **Save Record** and show the generated record ID and
      **Pending Review** state.
- [ ] Explain that save records reviewer input; it does not verify ownership.

### 3. Validate and inspect trust score

- [ ] Open **Land Registry** and select the newly saved record.
- [ ] Click **Run validation, reference and geometry checks**.
- [ ] Show the stored findings/alerts and the current status.
- [ ] Explain each score component (O, D, G, H), its fixed weight, input and
      contribution.
- [ ] Point out whether the score is complete or provisional and why.
- [ ] If appropriate, complete the human review only after the app permits it.

### 4. Parcel map

- [ ] Open **Geospatial Validation**.
- [ ] Show the valid, overlapping, and invalid geometry counts.
- [ ] Identify green valid, amber overlap, and red invalid sample parcels.
- [ ] State that the geometries are illustrative, not survey boundaries.

### 5. Alerts and audit

- [ ] Open **Alerts & Audit** and show the synthetic alerts and suggested
      actions.
- [ ] Open **Audit events**.
- [ ] Click **Verify audit-chain integrity** and show its genesis verification
      result.
- [ ] Explain the limitation: complete database rewrite plus recomputation can
      defeat a hash chain; this is not production blockchain.

## Scenario quick references

- `DEMO-0005`: owner field marked `Not detected`, requires manual correction.
- `DEMO-0006`: low OCR confidence and self-intersecting invalid sample shape.
- `DEMO-0003` and `DEMO-0004`: duplicate survey identifier and overlapping
  synthetic parcels.
- `DEMO-0007`: intentional owner mismatch against separate fictional reference
  data.
- `DEMO-0008` and `DEMO-0009`: another overlapping sample pair.
- `DEMO-0009`: low OCR confidence; `DEMO-0010`: high OCR confidence.

## End of demo/reset

- [ ] Do not reset unless the local demo history is meant to be deleted.
- [ ] For a fresh demo, run `uv run python scripts/seed_demo.py --reset`,
      read its warning, and type exactly `RESET DEMO` to confirm.
- [ ] Do not use `--yes` unless deletion is deliberately automated.
- [ ] Verify the reset command targets only `data/demo_land_records.sqlite3`
      and SQLite sidecars, never `data/land_records.sqlite3`.
