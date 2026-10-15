from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from html import escape
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
import folium
from streamlit_folium import st_folium

from modules.audit import list_events, verify_chain
from modules.database import (
    DatabasePath,
    create_land_record,
    get_land_record,
    initialize_database,
    list_all_alerts,
    list_land_records,
    resolve_alert,
)
from modules.extraction import extract_fields
from modules.geospatial import (
    evaluate_geospatial_records,
    load_geojson,
    styled_geojson,
)
from modules.ocr import (
    MAX_FILE_SIZE_MB,
    OCRError,
    get_pdf_page_count,
    is_tesseract_available,
    load_images_from_bytes,
    process_document,
)
from modules.record_review import prepare_reviewed_record
from modules.trust_score import calculate_trust_score, count_major_inconsistencies
from modules.validation import validate_record
from modules.review_workflow import (
    complete_human_review,
    dashboard_snapshot,
    run_record_validation,
)

APP_NAME = "Land Record Digitalization"
APP_ROOT = Path(__file__).resolve().parent
DATABASE_PATH: DatabasePath = Path(
    os.environ.get("LAND_RECORDS_DB", APP_ROOT / "data" / "land_records.sqlite3")
)
DEMO_DATA_DIR = APP_ROOT / "data" / "demo"
DEMO_MODE = os.environ.get("LAND_RECORDS_DEMO", "").strip().lower() in {
    "1", "true", "yes", "on"
}
DEMO_MODE = DEMO_MODE or Path(DATABASE_PATH).name == "demo_land_records.sqlite3"
PAGES = (
    "Overview Dashboard",
    "Digitize Record",
    "Land Registry",
    "Geospatial Validation",
    "Alerts & Audit",
)
_STATUS_LABELS = {
    "pending": "Pending Review",
    "validated": "Verified",
    "flagged": "Flagged",
}

st.set_page_config(
    page_title=APP_NAME,
    page_icon="🗺️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    :root {
        --navy: #17324d;
        --slate: #526477;
        --muted: #6b7c8d;
        --line: #dce4eb;
        --surface: #ffffff;
        --green: #26734d;
        --orange: #a75b16;
        --red: #a43737;
    }
    .stApp { background: #f4f7fa; color: var(--navy); }
    [data-testid="stSidebar"] { background: #edf2f6; border-right: 1px solid var(--line); }
    [data-testid="stSidebar"] h1 { color: var(--navy); font-size: 1.2rem; line-height: 1.35; }
    .page-eyebrow { color: var(--muted); font-size: .78rem; font-weight: 700;
                    letter-spacing: .1em; text-transform: uppercase; margin-bottom: .3rem; }
    .page-title { color: var(--navy); font-size: 2rem; font-weight: 700;
                  line-height: 1.2; margin: 0; }
    .page-description { color: var(--slate); font-size: 1rem; margin: .55rem 0 1.5rem; }
    .section-title { color: var(--navy); font-size: 1.15rem; font-weight: 650;
                     margin: 1.35rem 0 .6rem; }
    .metric-card, .placeholder-card {
        background: var(--surface); border: 1px solid var(--line); border-radius: 10px;
        padding: 1.05rem 1.2rem; min-height: 108px;
    }
    .metric-label { color: var(--muted); font-size: .83rem; font-weight: 600; }
    .metric-value { color: var(--navy); font-size: 1.85rem; font-weight: 700; margin-top: .3rem; }
    .status-pill { display: inline-block; border-radius: 999px; padding: .2rem .6rem;
                   font-size: .78rem; font-weight: 650; }
    .status-pending { background: #fff0dc; color: var(--orange); }
    .status-validated { background: #e4f3ea; color: var(--green); }
    .status-flagged { background: #fbe9e8; color: var(--red); }
    .severity-pill { display: inline-block; border-radius: 999px; padding: .18rem .55rem;
                     font-size: .76rem; font-weight: 700; }
    .severity-info { background: #e9eff5; color: #445a70; }
    .severity-low { background: #e8f3ee; color: #26734d; }
    .severity-medium { background: #fff0dc; color: #96520f; }
    .severity-high, .severity-critical { background: #fbe9e8; color: #a43737; }
    .demo-banner { background: #eef5fb; border-left: 4px solid #3d729b;
                   border-radius: 5px; padding: .7rem .9rem; margin: 0 0 1rem;
                   color: #29445c; }
    .demo-note { color: var(--muted); font-size: .78rem; }
    div[data-testid="stDataFrame"] { border: 1px solid var(--line); border-radius: 8px; }
    @media (max-width: 1100px) {
        .metric-value { font-size: 1.55rem; }
        .page-title { font-size: 1.7rem; }
        .metric-card { padding: .8rem .9rem; min-height: 94px; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def page_header(title: str, description: str) -> None:
    st.markdown(
        f'<div class="page-eyebrow">Smart India Hackathon 2026 · SIH26018</div>'
        f'<h1 class="page-title">{escape(title)}</h1>'
        f'<p class="page-description">{escape(description)}</p>',
        unsafe_allow_html=True,
    )


def status_label(status: str) -> None:
    safe_status = status if status in {"pending", "validated", "flagged"} else "pending"
    st.markdown(
        f'<span class="status-pill status-{safe_status}">'
        f'{escape(_STATUS_LABELS[safe_status])}</span>',
        unsafe_allow_html=True,
    )


def severity_label(severity: str) -> None:
    safe_severity = severity.lower() if severity.lower() in {
        "info", "low", "medium", "high", "critical"
    } else "info"
    st.markdown(
        f'<span class="severity-pill severity-{safe_severity}">'
        f'{escape(safe_severity.title())}</span>',
        unsafe_allow_html=True,
    )


def metric_card(label: str, value: int | str) -> None:
    st.markdown(
        f'<div class="metric-card"><div class="metric-label">{escape(label)}</div>'
        f'<div class="metric-value">{escape(str(value))}</div></div>',
        unsafe_allow_html=True,
    )


def _record_table(records: list[dict[str, Any]]) -> pd.DataFrame:
    columns = [
        "parcel_id",
        "survey_number",
        "owner_name",
        "district",
        "village",
        "land_use",
        "area",
        "area_unit",
        "document_year",
        "status",
        "record_source",
    ]
    if not records:
        return pd.DataFrame(columns=columns)
    prepared = [
        {
            **record,
            "record_source": (
                "Synthetic sample"
                if str(record.get("id", "")).startswith("DEMO-")
                else "User-reviewed input"
            ),
        }
        for record in records
    ]
    table = pd.DataFrame(prepared)[columns].copy()
    table["status"] = table["status"].map(_STATUS_LABELS)
    return table


def _reference_records() -> list[dict[str, Any]] | None:
    if not DEMO_MODE:
        return None
    path = DEMO_DATA_DIR / "reference_records.csv"
    try:
        table = pd.read_csv(path)
    except (OSError, ValueError, pd.errors.ParserError) as error:
        raise RuntimeError(f"Could not load fictional demo reference data: {error}") from error
    table["area"] = pd.to_numeric(table["area"], errors="coerce")
    return table.to_dict(orient="records")


def _parcel_feature_collection() -> dict[str, Any]:
    return load_geojson(DEMO_DATA_DIR / "parcel_geometries.geojson")


def _spatial_outcomes(records: list[dict[str, Any]]) -> dict[str, Any]:
    return evaluate_geospatial_records(records, _parcel_feature_collection())


def _outcome_for_record(
    record: dict[str, Any], outcomes: dict[str, Any]
) -> dict[str, Any]:
    direct = outcomes.get(record["id"])
    if direct is not None:
        return direct
    return {
        "score": None,
        "findings": [
            {
                "rule_code": "GEOSPATIAL_DATA_UNAVAILABLE",
                "severity": "info",
                "passed": False,
                "message": "No sample geometry matches this record; spatial validation is unavailable.",
            }
        ],
        "parcel_id": record.get("parcel_id"),
        "geometry_status": "unavailable",
    }


def overview_page() -> None:
    records = list_land_records(DATABASE_PATH, limit=1000)
    spatial = _spatial_outcomes(records) if DEMO_MODE else {}
    snapshot = dashboard_snapshot(
        DATABASE_PATH,
        reference_records=_reference_records(),
        geospatial_scores={
            record_id: outcome["score"] for record_id, outcome in spatial.items()
        }
        if DEMO_MODE
        else None,
    )
    page_header(
        "Overview Dashboard",
        "A central view of digitization activity and land-record review status.",
    )
    if DEMO_MODE:
        st.info(
            "Guided path: review this dashboard → Digitize Record (download and upload "
            "the fictional sample scan) → edit and save → Land Registry (run validation "
            "and inspect the trust breakdown) → Geospatial Validation → Alerts & Audit."
        )
    metric_columns = st.columns(4)
    for column, label, value in zip(
        metric_columns,
        ("Records in registry", "Pending review", "Flagged", "Verified"),
        (
            snapshot["total"],
            snapshot["pending_review"],
            snapshot["flagged"],
            snapshot["verified"],
        ),
    ):
        with column:
            metric_card(label, value)

    score_columns = st.columns(2)
    with score_columns[0]:
        average = snapshot["average_complete_trust_score"]
        metric_card(
            "Average complete trust score",
            f"{average:.1f} / 100" if average is not None else "Unavailable",
        )
        st.caption(
            f'{snapshot["complete_score_count"]} complete score(s); '
            f'{snapshot["provisional_score_count"]} record(s) have provisional subtotals.'
        )
    with score_columns[1]:
        subtotal = snapshot["average_provisional_subtotal"]
        metric_card(
            "Average provisional subtotal",
            f"{subtotal:.1f} / 100" if subtotal is not None else "Unavailable",
        )
        st.caption(
            "Provisional subtotals are not complete trust scores; missing components "
            "are not treated as successful."
        )
    if snapshot["complete_score_count"]:
        st.markdown('<div class="section-title">Trust-score categories</div>', unsafe_allow_html=True)
        category_columns = st.columns(3)
        for column, category in zip(
            category_columns,
            ("High confidence", "Review recommended", "High risk / manual review"),
        ):
            with column:
                metric_card(category, snapshot["trust_score_categories"][category])

    st.markdown('<div class="section-title">Recently added records</div>', unsafe_allow_html=True)
    if records:
        recent_records = _record_table(records[-10:][::-1])[
            ["parcel_id", "survey_number", "owner_name", "district", "status", "record_source"]
        ]
        st.dataframe(
            recent_records,
            width="stretch",
            hide_index=True,
            column_config={
                "parcel_id": st.column_config.TextColumn("Parcel ID"),
                "survey_number": st.column_config.TextColumn("Survey number"),
                "owner_name": st.column_config.TextColumn("Owner"),
                "document_year": st.column_config.NumberColumn("Document year", format="%d"),
                "record_source": st.column_config.TextColumn("Record source"),
            },
        )
    else:
        st.info(
            "No land records are stored yet. Upload a scanned record under Digitize Record; "
            "a saved record remains pending until validation and human review."
        )

    alert_column, activity_column = st.columns(2)
    with alert_column:
        st.markdown('<div class="section-title">Recent alerts</div>', unsafe_allow_html=True)
        if snapshot["recent_alerts"]:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Parcel": alert["parcel_id"],
                            "Category": alert["category"],
                            "Severity": alert["severity"].title(),
                            "Reason": alert["message"],
                            "Status": "Resolved" if alert["resolved"] else "Open",
                        }
                        for alert in snapshot["recent_alerts"]
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
        else:
            st.info("No alerts have been generated.")
    with activity_column:
        st.markdown('<div class="section-title">Recent record activity</div>', unsafe_allow_html=True)
        if snapshot["recent_activity"]:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Parcel": event["parcel_id"],
                            "Action": event["action"].replace("_", " ").title(),
                            "Time (UTC)": event["timestamp"],
                            "Actor": event["actor"],
                        }
                        for event in snapshot["recent_activity"]
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
        else:
            st.info("No audit activity has been recorded.")

    st.caption(
        "Verified means required checks passed and a reviewer completed the review. "
        "It does not establish legal ownership."
    )


def digitize_page() -> None:
    page_header(
        "Digitize Record",
        "Upload a scanned land document, inspect OCR output, and review editable field suggestions.",
    )
    st.caption(
        "OCR and extracted values are suggestions only. They do not establish ownership "
        "and are not saved to the registry."
    )
    if DEMO_MODE and (DEMO_DATA_DIR / "sample-land-record.png").is_file():
        st.download_button(
            "Download fictional sample scan",
            data=(DEMO_DATA_DIR / "sample-land-record.png").read_bytes(),
            file_name="sample-land-record.png",
            mime="image/png",
            help="Synthetic sample only; upload it here to follow the guided demonstration.",
        )
    uploaded_file = st.file_uploader(
        "Choose a land-record document",
        type=("png", "jpg", "jpeg", "pdf"),
        accept_multiple_files=False,
        max_upload_size=MAX_FILE_SIZE_MB,
        help=f"PNG, JPG, or scanned PDF; maximum {MAX_FILE_SIZE_MB} MB.",
    )
    if uploaded_file is None:
        st.info("Upload a PNG, JPG, or scanned PDF to begin.")
        return

    filename = uploaded_file.name
    document_bytes = uploaded_file.getvalue()
    fingerprint = hashlib.sha256(document_bytes).hexdigest()
    preview_page = 0

    try:
        if Path(filename).suffix.lower() == ".pdf":
            page_count = get_pdf_page_count(filename, document_bytes)
            selected_page = st.number_input(
                "PDF page to process",
                min_value=1,
                max_value=page_count,
                value=1,
                step=1,
            )
            preview_page = int(selected_page) - 1
        preview_images = load_images_from_bytes(
            filename, document_bytes, pdf_pages=[preview_page]
        )
    except OCRError as error:
        st.error(str(error))
        return

    run_key = f"{fingerprint}:{preview_page}"
    active_run_key = st.session_state.get("digitize_run_key")
    if active_run_key != run_key:
        st.session_state.pop("digitize_result", None)
        st.session_state.pop("digitize_extraction", None)
        st.session_state["digitize_run_key"] = run_key

    options_column, action_column = st.columns((3, 2))
    with options_column:
        language = st.text_input(
            "Tesseract language code(s)",
            value="eng",
            help="Join installed language codes with '+', for example: eng+hin.",
        )
    with action_column:
        preprocess = st.checkbox("Preprocess image", value=True)
        use_threshold = st.checkbox("Adaptive thresholding", value=False)

    preview_column, output_column = st.columns(2, gap="large")
    with preview_column:
        st.markdown("#### Document preview")
        st.image(preview_images[0], width="stretch")
        st.caption(
            f"{filename} · {len(document_bytes) / 1024:.0f} KB"
            + (f" · page {preview_page + 1} of {page_count}" if Path(filename).suffix.lower() == ".pdf" else "")
        )
    with output_column:
        st.markdown("#### OCR output")
        if not is_tesseract_available():
            st.warning(
                "Tesseract executable is unavailable. Install it separately "
                "(macOS: `brew install tesseract`) or configure `TESSERACT_CMD`."
            )
        if st.button("Run / rerun OCR", type="primary", width="stretch"):
            try:
                with st.spinner("Reading the document and running Tesseract OCR…"):
                    result = process_document(
                        filename,
                        document_bytes,
                        lang=language.strip(),
                        pdf_pages=[preview_page],
                        preprocess=preprocess,
                        adaptive_threshold=use_threshold,
                    )
                extracted = extract_fields(result["combined_text"])
                original_key = f"digitize_original_extraction_{fingerprint}_{preview_page}"
                st.session_state.setdefault(original_key, extracted.to_dict())
                st.session_state["digitize_result"] = result
                st.session_state["digitize_extraction"] = extracted.to_dict()
            except OCRError as error:
                st.error(str(error))
            else:
                st.rerun()

        result = st.session_state.get("digitize_result")
        extraction = st.session_state.get("digitize_extraction")
        if result is not None:
            confidence = result["mean_confidence"]
            if confidence is None:
                st.caption("OCR confidence: unavailable from the engine")
            else:
                st.metric("Mean OCR confidence", f"{confidence:.1f} / 100")
            st.text_area(
                "Raw recognized text",
                value=result["combined_text"],
                height=270,
                key=f"raw_ocr_{run_key}",
                help="Review this text to spot OCR mistakes.",
            )
        else:
            st.caption("Run OCR to display recognized text and field suggestions.")

    if extraction is not None:
        st.markdown("### Review extracted fields")
        st.caption("Stage 1 · OCR-extracted suggestions. Review each value before saving.")
        st.caption(
            "Rule-match estimates are not probabilities. Saving creates a pending record, "
            "not a verified land claim."
        )
        workflow_key = f"{fingerprint}_{preview_page}"
        original_key = f"digitize_original_extraction_{workflow_key}"
        st.session_state.setdefault(original_key, dict(extraction))
        original_extraction = st.session_state[original_key]
        record_id_key = f"digitize_record_id_{workflow_key}"
        st.session_state.setdefault(record_id_key, str(uuid.uuid4()))
        saved_record = st.session_state.get(f"digitize_saved_record_{workflow_key}")
        validation_errors = st.session_state.get(
            f"digitize_validation_errors_{workflow_key}", {}
        )

        field_specs = (
            ("parcel_id", "Parcel ID"),
            ("survey_number", "Survey number"),
            ("owner_name", "Owner name"),
            ("district", "District"),
            ("village", "Village"),
            ("land_use", "Land use"),
            ("area", "Area (numeric; unit not normalized)"),
            ("area_unit", "Area unit (if recognized)"),
            ("document_year", "Document year"),
        )
        with st.expander("Original extracted values (read-only)", expanded=False):
            st.json(
                {
                    key: value
                    for key, value in original_extraction.items()
                    if key != "field_confidences"
                }
            )
            st.caption(
                "These are the initial OCR-derived suggestions. Edits below do not change this snapshot."
            )

        form_values: dict[str, str] = {}
        st.markdown("#### Stage 2 · Reviewer-entered values")
        with st.form(f"review_record_{workflow_key}"):
            record_id = st.text_input(
                "Record ID (optional)",
                key=record_id_key,
                help="A unique ID is generated for this review. You may replace it with an available ID.",
            )
            if message := validation_errors.get("id"):
                st.error(message)
            columns = st.columns(2)
            for index, (field_name, label) in enumerate(field_specs):
                extracted_value = extraction.get(field_name)
                score = extraction.get("field_confidences", {}).get(field_name)
                widget_key = f"digitize_{workflow_key}_{field_name}"
                with columns[index % 2]:
                    form_values[field_name] = st.text_input(
                        label,
                        value="" if extracted_value is None else str(extracted_value),
                        key=widget_key,
                        placeholder="Not detected — enter value",
                    )
                    if message := validation_errors.get(field_name):
                        st.error(message)
                    if extracted_value is None:
                        st.caption("Not detected — manual entry required if required.")
                    elif score is not None and score < 60:
                        st.caption(f"Uncertain match · {score:.0f}/100 — please review")
                    elif score is not None:
                        st.caption(f"Review suggested · rule-match estimate {score:.0f}/100")

            save_clicked = st.form_submit_button(
                "Save Record",
                type="primary",
                width="stretch",
                disabled=saved_record is not None,
            )

        if save_clicked:
            reviewed_values = {
                **form_values,
                "id": record_id,
            }
            payload, errors = prepare_reviewed_record(
                reviewed_values,
                ocr_confidence=result["mean_confidence"],
                source_document_hash=fingerprint,
            )
            if errors:
                st.session_state[f"digitize_validation_errors_{workflow_key}"] = errors
                st.rerun()
            else:
                st.session_state.pop(
                    f"digitize_validation_errors_{workflow_key}", None
                )
                try:
                    stored_record = create_land_record(DATABASE_PATH, payload)
                except sqlite3.IntegrityError as error:
                    if payload.get("id") and get_land_record(DATABASE_PATH, payload["id"]):
                        st.session_state[f"digitize_validation_errors_{workflow_key}"] = {
                            "id": "This record ID already exists. Enter a unique ID."
                        }
                        st.rerun()
                    else:
                        st.error(f"Could not save record because of a database constraint: {error}")
                except (TypeError, ValueError) as error:
                    st.error(f"Record data was rejected: {error}")
                except sqlite3.Error as error:
                    st.error(f"Database error while saving the record: {error}")
                else:
                    st.session_state[f"digitize_saved_record_{workflow_key}"] = stored_record
                    st.session_state.pop(
                        f"digitize_validation_errors_{workflow_key}", None
                    )
                    st.rerun()

        validation_errors = st.session_state.get(
            f"digitize_validation_errors_{workflow_key}", {}
        )
        if message := validation_errors.get("ocr_confidence"):
            st.error(f"OCR confidence: {message}")
        if message := validation_errors.get("source_document_hash"):
            st.error(f"Source document: {message}")

        if saved_record is not None:
            st.success(
                f"Record saved as {saved_record['id']} with status "
                f"{_STATUS_LABELS[saved_record['status']]}."
            )
            st.caption(
                "Saved means entered into this local review database; it does not mean verified or prove ownership."
            )
            st.success(
                "Record creation was added to the global hash-chained audit history. "
                "This entry is tamper-evident, but its hash is not proof of ownership."
            )


def registry_page() -> None:
    records = list_land_records(DATABASE_PATH, limit=1000)
    references = _reference_records()
    spatial = _spatial_outcomes(records) if DEMO_MODE else {}
    page_header(
        "Land Registry",
        "Browse records currently stored in this local MVP database.",
    )
    if records:
        st.dataframe(
            _record_table(records),
            width="stretch",
            hide_index=True,
            column_config={
                "parcel_id": st.column_config.TextColumn("Parcel ID"),
                "survey_number": st.column_config.TextColumn("Survey number"),
                "owner_name": st.column_config.TextColumn("Owner"),
                "district": st.column_config.TextColumn("District"),
                "village": st.column_config.TextColumn("Village"),
                "land_use": st.column_config.TextColumn("Land use"),
                "area": st.column_config.NumberColumn("Area", format="%.2f"),
                "area_unit": st.column_config.TextColumn("Area unit"),
                "document_year": st.column_config.NumberColumn("Document year", format="%d"),
                "status": st.column_config.TextColumn("Status"),
                "record_source": st.column_config.TextColumn("Record source"),
            },
        )
        with st.expander("Selected status labels"):
            for status in ("pending", "validated", "flagged"):
                status_label(status)
        st.markdown('<div class="section-title">Record details</div>', unsafe_allow_html=True)
        selected_id = st.selectbox(
            "Select a record",
            options=[record["id"] for record in records],
            format_func=lambda record_id: next(
                f"{record['parcel_id']} · {record['owner_name']} · "
                f'{"Sample" if record_id.startswith("DEMO-") else "Reviewed"}'
                for record in records
                if record["id"] == record_id
            ),
            key="registry_record_details_id",
        )
        selected_record = get_land_record(DATABASE_PATH, selected_id)
        if selected_record is not None:
            details_column, score_column = st.columns((3, 2))
            with details_column:
                status_label(selected_record["status"])
                st.json(
                    {
                        key: selected_record[key]
                        for key in (
                            "id", "parcel_id", "survey_number", "owner_name",
                            "district", "village", "land_use", "area", "area_unit",
                            "document_year", "ocr_confidence", "status",
                            "human_reviewed", "reviewed_at",
                        )
                    },
                    expanded=True,
                )

            if st.button(
                "Run validation, reference and geometry checks",
                key=f"run_validation_{selected_id}",
                help="Run available local checks, store findings, generate deduplicated alerts, and log the run.",
            ):
                try:
                    outcome = _outcome_for_record(selected_record, spatial)
                    run_result = run_record_validation(
                        DATABASE_PATH,
                        selected_id,
                        reference_records=references,
                        geospatial_findings=outcome["findings"],
                        geospatial_score=outcome["score"],
                    )
                except (sqlite3.Error, TypeError, ValueError) as error:
                    st.error(f"Could not complete the validation run: {error}")
                else:
                    if run_result["audit_logged"]:
                        st.success(
                            f'Validation run stored; {len(run_result["alerts"])} related '
                            "alert(s) are open or already unresolved."
                        )
                    else:
                        st.error(
                            "Validation findings were stored, but the audit event could not "
                            "be appended because the audit chain is invalid. See the Audit tab."
                        )
                    selected_record = get_land_record(DATABASE_PATH, selected_id)
                    status_label(selected_record["status"])
            checks = validate_record(
                selected_record,
                existing_records=records,
                reference_records=references,
            )
            spatial_outcome = _outcome_for_record(selected_record, spatial)
            checks.extend(spatial_outcome["findings"])
            if selected_record["status"] != "validated":
                if st.button(
                    "Complete human review",
                    key=f"complete_human_review_{selected_id}",
                    help="Marks the record verified only after required checks pass and blocking alerts are resolved.",
                ):
                    try:
                        reviewed_record = complete_human_review(
                            DATABASE_PATH, selected_id
                        )
                    except (sqlite3.Error, TypeError, ValueError) as error:
                        st.error(f"Human review is not complete: {error}")
                    else:
                        st.success("Human review completed. The record is now Verified.")
                        selected_record = reviewed_record
                        status_label(selected_record["status"])
            major_inconsistencies = count_major_inconsistencies(
                finding
                for finding in checks
                if not str(finding["rule_code"]).startswith(("GEO_", "GEOSPATIAL_"))
            )
            audit_status = verify_chain(DATABASE_PATH)
            audit_score = (
                100.0
                if audit_status["valid"] and audit_status["event_count"] > 0
                else 0.0
                if not audit_status["valid"] and audit_status["event_count"] > 0
                else None
            )
            trust = calculate_trust_score(
                ocr_confidence=selected_record["ocr_confidence"],
                major_inconsistencies=major_inconsistencies,
                geospatial_score=spatial_outcome["score"],
                audit_integrity_score=audit_score,
            )
            with score_column:
                st.subheader("Software verification indicator")
                st.metric(
                    "Provisional weighted subtotal" if not trust["is_complete"] else "Verification score",
                    f'{trust["score"]:.1f} / 100',
                )
                if trust["category"] == "High confidence":
                    st.success(trust["category"])
                elif trust["category"] == "High risk / manual review":
                    st.error(trust["category"])
                else:
                    st.warning(trust["category"])
                st.caption(trust["explanation"])
                st.caption(trust["data_consistency_calculation"])
                st.caption(
                    f'Sample geometry check: {spatial_outcome["geometry_status"]}. '
                    "This evaluates only the illustrative GeoJSON shipped with the demo."
                )
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "Component": component["label"],
                                "Weight": f'{component["weight"]:.0%}',
                                "Input score": (
                                    "Unavailable"
                                    if component["input_score"] is None
                                    else f'{component["input_score"]:.1f}'
                                ),
                                "Contribution": f'{component["weighted_contribution"]:.2f}',
                            }
                            for component in trust["components"].values()
                        ]
                    ),
                    hide_index=True,
                    width="stretch",
                )
                st.caption(
                    "D starts at 100 and deducts 25 points per distinct failed critical "
                    "data-validation issue. Geospatial findings contribute to G rather "
                    "than D; warning-level duplicates are not major inconsistencies."
                )
            st.info(
                "This software indicator is not a probability of legal ownership, a legal "
                "certificate, proof of fraud, or legal boundary decision. Demo spatial "
                "results use fictional sample geometry only."
            )
    else:
        st.info(
            "The registry is empty. Use Digitize Record to upload a scan, review the "
            "suggested fields, and save a pending record."
        )
    st.caption("Local records are not an authoritative land registry.")


def geospatial_page() -> None:
    page_header(
        "Geospatial Validation",
        "Review geometry validity and overlaps in the included fictional sample parcels.",
    )
    st.warning(
        "Illustrative synthetic geometries only. These are not survey boundaries, "
        "authoritative GIS data, or evidence of ownership."
    )
    try:
        collection = _parcel_feature_collection()
        records = list_land_records(DATABASE_PATH, limit=1000)
        map_records = records
        if not map_records:
            map_records = [
                {
                    "id": feature["properties"].get("record_id", ""),
                    "parcel_id": feature["properties"].get("parcel_id", ""),
                }
                for feature in collection["features"]
            ]
        outcomes = evaluate_geospatial_records(map_records, collection)
        displayed_geojson = styled_geojson(collection, map_records)
    except (OSError, TypeError, ValueError) as error:
        st.error(f"Could not load the sample parcel map: {error}")
        return

    statuses = [outcome["geometry_status"] for outcome in outcomes.values()]
    counts = {
        "Valid": statuses.count("valid"),
        "Overlapping": statuses.count("overlap"),
        "Invalid": statuses.count("invalid"),
        "Unavailable": statuses.count("unavailable"),
    }
    count_columns = st.columns(4)
    for column, (label, value) in zip(count_columns, counts.items()):
        with column:
            metric_card(label, value)

    feature_collection = folium.Map(
        location=[28.007, 77.085],
        zoom_start=13,
        tiles=None,
        control_scale=True,
    )
    folium.TileLayer(
        tiles=(
            "https://server.arcgisonline.com/ArcGIS/rest/services/"
            "World_Imagery/MapServer/tile/{z}/{y}/{x}"
        ),
        attr=(
            "Tiles &copy; Esri — Source: Esri, Maxar, Earthstar Geographics, "
            "and the GIS User Community"
        ),
        name="Esri World Imagery",
    ).add_to(feature_collection)
    folium.GeoJson(
        displayed_geojson,
        name="Fictional sample parcels",
        style_function=lambda feature: {
            "color": feature["properties"]["map_color"],
            "fillColor": feature["properties"]["map_color"],
            "weight": 2,
            "fillOpacity": 0.28,
        },
        tooltip=folium.GeoJsonTooltip(
            fields=["parcel_id", "review_label", "village"],
            aliases=["Parcel", "Geometry review", "Fictional village"],
            sticky=True,
        ),
    ).add_to(feature_collection)
    st_folium(
        feature_collection,
        width="stretch",
        height=500,
        returned_objects=[],
    )
    st.caption(
        "Esri World Imagery is an external satellite tile service and requires "
        "internet access. Green = valid, amber = overlap warning, red = invalid "
        "geometry; the sample parcel shapes remain illustrative, not authoritative."
    )
    feature_by_record_id = {
        feature["properties"].get("record_id"): feature
        for feature in collection["features"]
    }
    geometry_rows = []
    for record_id, outcome in outcomes.items():
        feature = feature_by_record_id.get(record_id, {})
        properties = feature.get("properties", {})
        geometry_rows.append(
            {
                "Parcel ID": properties.get("parcel_id", outcome["parcel_id"]),
                "Village (fictional)": properties.get("village", ""),
                "Geometry result": outcome["geometry_status"].title(),
                "G component": (
                    "Unavailable" if outcome["score"] is None else f'{outcome["score"]:.0f}/100'
                ),
                "Finding": outcome["findings"][0]["message"],
            }
        )
    st.dataframe(pd.DataFrame(geometry_rows), hide_index=True, width="stretch")


def alerts_audit_page() -> None:
    records = list_land_records(DATABASE_PATH, limit=1000)
    parcel_names = {record["id"]: record["parcel_id"] for record in records}
    stored_alerts = list_all_alerts(DATABASE_PATH)
    alerts = [
        {
            **alert,
            "parcel_id": parcel_names.get(alert["record_id"], alert["record_id"]),
        }
        for alert in stored_alerts
    ]
    audit_events = list_events(DATABASE_PATH)
    page_header(
        "Alerts & Audit",
        "Review alerts and the global hash-chained audit timeline, and verify its integrity.",
    )
    alert_tab, audit_tab = st.tabs(["Alerts", "Audit events"])
    with alert_tab:
        unresolved = [alert for alert in alerts if not alert["resolved"]]
        metric_card("Unresolved alerts", len(unresolved))
        if alerts:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Parcel ID": alert["parcel_id"],
                            "Category": alert["category"],
                            "Severity": alert["severity"].title(),
                            "Message": alert["message"],
                            "Suggested action": alert["suggested_action"],
                            "Resolved": "Yes" if alert["resolved"] else "No",
                            "Created": alert["created_at"],
                        }
                        for alert in alerts
                    ]
                ),
                width="stretch",
                hide_index=True,
            )
            if unresolved:
                st.markdown(
                    '<div class="section-title">Resolve an alert after review</div>',
                    unsafe_allow_html=True,
                )
                for alert in unresolved:
                    with st.container(border=True):
                        st.markdown(
                            f'**{escape(alert["category"])} · Parcel '
                            f'{escape(alert["parcel_id"])}**'
                        )
                        severity_label(alert["severity"])
                        st.write(alert["message"])
                        st.caption(f'Suggested action: {alert["suggested_action"]}')
                        if st.button(
                            "Mark resolved",
                            key=f"resolve_alert_{alert['id']}",
                        ):
                            try:
                                resolve_alert(DATABASE_PATH, alert["id"])
                            except (sqlite3.Error, TypeError, ValueError) as error:
                                st.error(f"Alert was not resolved: {error}")
                            else:
                                st.success(
                                    "Alert resolution recorded. Historical alert information is retained."
                                )
        else:
            st.info("No alerts are stored.")
    with audit_tab:
        if st.button("Verify audit-chain integrity", key="verify_audit_chain"):
            verification = verify_chain(DATABASE_PATH)
            if verification["valid"]:
                st.success(
                    f"Audit chain valid: {verification['event_count']} event(s) verified from genesis."
                )
            else:
                invalid = verification["first_invalid_event"]
                location = (
                    f"position {invalid['chain_position']} · event {invalid['event_id']}"
                    if invalid is not None
                    else "audit-chain head"
                )
                st.error(f"Audit chain invalid at {location}: {verification['reason']}")
        if audit_events:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Position": event["chain_position"],
                            "Parcel ID": parcel_names.get(event["record_id"], event["record_id"]),
                            "Action": event["action"],
                            "Timestamp (UTC)": event["timestamp"],
                            "Actor": event["actor"],
                            "Payload": json.dumps(event["payload"], sort_keys=True, ensure_ascii=False),
                            "Previous hash": event["previous_hash"],
                            "Hash": event["event_hash"],
                        }
                        for event in audit_events
                    ]
                ),
                width="stretch",
                hide_index=True,
            )
        else:
            st.info("No audit events are stored.")
        st.warning(
            "This is a hash-chained tamper-evident audit log, not a production blockchain. "
            "It cannot protect against an attacker who can rewrite the entire database "
            "and recompute every hash."
        )


with st.sidebar:
    st.title(APP_NAME)
    st.caption("Intelligent land-record review")
    if DEMO_MODE:
        st.markdown(
            '<span class="status-pill status-pending">DEMO · synthetic dataset</span>',
            unsafe_allow_html=True,
        )
        st.caption("All sample people, records, references and boundaries are fictional.")
    else:
        st.caption("Local database · not an authoritative registry")
    st.markdown("---")
    selected_page = st.radio("Navigation", PAGES, label_visibility="collapsed")
    st.markdown("---")
    st.markdown(
        '<div class="demo-note">Local MVP · sample datasets are illustrative, never authoritative.</div>',
        unsafe_allow_html=True,
    )

initialize_database(DATABASE_PATH)

if DEMO_MODE:
    st.markdown(
        '<div class="demo-banner"><strong>DEMO MODE · ILLUSTRATIVE DATA ONLY</strong>'
        ' Synthetic land records, reference rows and geometries are fictional. '
        "They are not official records, authoritative boundaries, legal evidence, "
        "or proof of ownership.</div>",
        unsafe_allow_html=True,
    )

if selected_page == "Overview Dashboard":
    overview_page()
elif selected_page == "Digitize Record":
    digitize_page()
elif selected_page == "Land Registry":
    registry_page()
elif selected_page == "Geospatial Validation":
    geospatial_page()
else:
    alerts_audit_page()
