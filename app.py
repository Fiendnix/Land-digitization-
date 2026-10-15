from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from modules.database import (
    DatabasePath,
    initialize_database,
    list_alerts,
    list_audit_events,
    list_land_records,
)

APP_NAME = "Land Record Digitalization"
DATABASE_PATH: DatabasePath = Path(__file__).parent / "data" / "land_records.sqlite3"
PAGES = (
    "Overview Dashboard",
    "Digitize Record",
    "Land Registry",
    "Geospatial Validation",
    "Alerts & Audit",
)

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
    .demo-note { color: var(--muted); font-size: .78rem; }
    div[data-testid="stDataFrame"] { border: 1px solid var(--line); border-radius: 8px; }
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
        f'<span class="status-pill status-{safe_status}">{escape(safe_status.title())}</span>',
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
        "document_year",
        "status",
    ]
    if not records:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(records)[columns]


def overview_page() -> None:
    records = list_land_records(DATABASE_PATH, limit=1000)
    counts = {status: sum(record["status"] == status for record in records) for status in
              ("pending", "validated", "flagged")}
    page_header(
        "Overview Dashboard",
        "A central view of digitization activity and land-record review status.",
    )
    metric_columns = st.columns(4)
    for column, label, value in zip(
        metric_columns,
        ("Records in registry", "Pending review", "Validated", "Flagged"),
        (len(records), counts["pending"], counts["validated"], counts["flagged"]),
    ):
        with column:
            metric_card(label, value)

    st.markdown('<div class="section-title">Recently added records</div>', unsafe_allow_html=True)
    if records:
        st.dataframe(
            _record_table(records[-10:][::-1]),
            use_container_width=True,
            hide_index=True,
            column_config={
                "parcel_id": st.column_config.TextColumn("Parcel ID"),
                "survey_number": st.column_config.TextColumn("Survey number"),
                "owner_name": st.column_config.TextColumn("Owner"),
                "document_year": st.column_config.NumberColumn("Document year", format="%d"),
            },
        )
    else:
        st.info("No land records have been added yet. Use Digitize Record when record intake is available.")

    st.caption(
        "This dashboard summarizes local application data only. A record status does not establish ownership."
    )


def digitize_page() -> None:
    page_header(
        "Digitize Record",
        "Document intake and field extraction are planned; no OCR or record creation is active yet.",
    )
    st.markdown(
        """
        <div class="placeholder-card">
          <div class="section-title" style="margin-top:0">Document intake — coming soon</div>
          <p>Upload handling, image preprocessing, OCR extraction, and review-before-save
          are not implemented in this MVP shell.</p>
          <p class="demo-note">No document is uploaded or stored from this page.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.info("OCR output will require human review. OCR does not prove ownership.")


def registry_page() -> None:
    records = list_land_records(DATABASE_PATH, limit=1000)
    page_header(
        "Land Registry",
        "Browse records currently stored in this local MVP database.",
    )
    if records:
        st.dataframe(
            _record_table(records),
            use_container_width=True,
            hide_index=True,
            column_config={
                "parcel_id": st.column_config.TextColumn("Parcel ID"),
                "survey_number": st.column_config.TextColumn("Survey number"),
                "owner_name": st.column_config.TextColumn("Owner"),
                "district": st.column_config.TextColumn("District"),
                "village": st.column_config.TextColumn("Village"),
                "land_use": st.column_config.TextColumn("Land use"),
                "area": st.column_config.NumberColumn("Area", format="%.2f"),
                "document_year": st.column_config.NumberColumn("Document year", format="%d"),
                "status": st.column_config.TextColumn("Status"),
            },
        )
        with st.expander("Selected status labels"):
            for status in ("pending", "validated", "flagged"):
                status_label(status)
    else:
        st.info("The registry is empty. Record creation is not available in this interface yet.")
    st.caption("Local records are not an authoritative land registry.")


def geospatial_page() -> None:
    page_header(
        "Geospatial Validation",
        "Parcel geometry and spatial checks are not implemented in this application shell.",
    )
    st.markdown(
        """
        <div class="placeholder-card">
          <div class="section-title" style="margin-top:0">Spatial review — coming soon</div>
          <p>Map display, geometry validation, and boundary comparisons are planned
          for a later step.</p>
          <p class="demo-note">No map or GIS validation is being performed.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.warning("Any future sample GIS data must be treated as illustrative, not authoritative.")


def alerts_audit_page() -> None:
    records = list_land_records(DATABASE_PATH, limit=1000)
    alerts = [
        {**alert, "parcel_id": record["parcel_id"]}
        for record in records
        for alert in list_alerts(DATABASE_PATH, record["id"])
    ]
    audit_events = [
        {**event, "parcel_id": record["parcel_id"]}
        for record in records
        for event in list_audit_events(DATABASE_PATH, record["id"])
    ]
    page_header(
        "Alerts & Audit",
        "Review stored alerts and audit-event payloads. Hash-chain generation is not active.",
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
                            "Resolved": "Yes" if alert["resolved"] else "No",
                            "Created": alert["created_at"],
                        }
                        for alert in alerts
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No alerts are stored.")
    with audit_tab:
        if audit_events:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Parcel ID": event["parcel_id"],
                            "Action": event["action"],
                            "Timestamp (UTC)": event["event_timestamp"],
                            "Payload": json.dumps(
                                json.loads(event["canonical_payload"]),
                                sort_keys=True,
                                ensure_ascii=False,
                            ),
                            "Hash": event["event_hash"] or "Not generated",
                        }
                        for event in audit_events
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No audit events are stored.")
        st.caption("Events shown here are stored payloads; this page does not verify a hash chain.")


with st.sidebar:
    st.title(APP_NAME)
    st.caption("Intelligent land-record review")
    st.markdown(
        '<span class="status-pill status-pending">DEMO · local database</span>',
        unsafe_allow_html=True,
    )
    st.markdown("---")
    selected_page = st.radio("Navigation", PAGES, label_visibility="collapsed")
    st.markdown("---")
    st.markdown(
        '<div class="demo-note">Prototype interface · features are clearly marked until implemented.</div>',
        unsafe_allow_html=True,
    )

initialize_database(DATABASE_PATH)

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
