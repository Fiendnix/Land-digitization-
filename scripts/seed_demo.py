from __future__ import annotations

import argparse
import csv
import random
import sqlite3
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.database import (
    DatabasePath,
    create_land_record,
    initialize_database,
    list_land_records,
)
from modules.geospatial import evaluate_geospatial_records, load_geojson
from modules.review_workflow import run_record_validation

DEMO_DIR = ROOT / "data" / "demo"
DEFAULT_DATABASE = ROOT / "data" / "demo_land_records.sqlite3"
SAMPLE_DOCUMENT = DEMO_DIR / "sample-land-record.png"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _reference_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in _read_csv(DEMO_DIR / "reference_records.csv"):
        rows.append(
            {
                **row,
                "area": float(row["area"]),
            }
        )
    return rows


def _demo_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in _read_csv(DEMO_DIR / "records.csv"):
        records.append(
            {
                **row,
                "area": float(row["area"]),
                "document_year": int(row["document_year"]),
                "ocr_confidence": float(row["ocr_confidence"]),
            }
        )
    return records


def _create_sample_document() -> None:
    """Create a clean, fictional scan-like PNG using deterministic noise."""
    image = Image.new("RGB", (1800, 1120), "#fffdf7")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=38)
    title_font = ImageFont.load_default(size=48)
    draw.rectangle((50, 45, 1750, 1070), outline="#35475a", width=4)
    draw.text((100, 90), "SYNTHETIC DEMONSTRATION DOCUMENT", fill="#20364d", font=title_font)
    draw.text((100, 160), "NOT A GOVERNMENT RECORD · FICTIONAL SAMPLE DATA", fill="#9b3b32", font=font)
    draw.line((100, 225, 1690, 225), fill="#65788a", width=3)
    fields = (
        "Parcel ID: DEMO-PARCEL-001",
        "Survey Number: DEMO-SURVEY-001",
        "Owner Name: Avery Sample",
        "District: Demo District",
        "Village: Maple Example",
        "Land Use: Agricultural",
        "Area: 2.5 hectares",
        "Document Year: 2022",
    )
    for index, line in enumerate(fields):
        draw.text((120, 280 + index * 78), line, fill="#1f2933", font=font)
    draw.line((100, 950, 1690, 950), fill="#9aa6b2", width=2)
    draw.text((100, 975), "For software demonstration only. Does not establish ownership.", fill="#526477", font=font)

    random_source = random.Random(26018)
    pixels = image.load()
    for _ in range(1800):
        x = random_source.randrange(65, 1735)
        y = random_source.randrange(60, 1055)
        shade = random_source.randrange(225, 250)
        pixels[x, y] = (shade, shade, shade)
    image.save(SAMPLE_DOCUMENT, format="PNG", optimize=False)


def seed_demo_database(
    database_path: DatabasePath = DEFAULT_DATABASE,
    *,
    create_sample_document: bool = True,
) -> dict[str, Any]:
    """Seed all sample rows once; refuse ambiguous partial reseeds."""
    initialize_database(database_path)
    source_rows = _demo_records()
    expected_ids = {str(row["id"]) for row in source_rows}
    current_records = list_land_records(database_path, limit=1000)
    existing_ids = {str(record["id"]) for record in current_records}
    existing_demo_ids = expected_ids & existing_ids

    if existing_demo_ids and existing_demo_ids != expected_ids:
        missing = sorted(expected_ids - existing_demo_ids)
        raise RuntimeError(
            "The demo dataset is only partially present. No data was changed. "
            f"Missing demo IDs: {', '.join(missing)}. Use the explicit demo reset procedure."
        )

    inserted_count = 0
    if not existing_demo_ids:
        for fields in source_rows:
            fields["status"] = "pending"
            create_land_record(database_path, fields)
            inserted_count += 1

        records = [
            item
            for item in list_land_records(database_path, limit=1000)
            if item["id"] in expected_ids
        ]
        references = _reference_rows()
        geojson = load_geojson(DEMO_DIR / "parcel_geometries.geojson")
        spatial = evaluate_geospatial_records(records, geojson)
        for record in records:
            outcome = spatial[record["id"]]
            run_record_validation(
                database_path,
                record["id"],
                reference_records=references,
                geospatial_findings=outcome["findings"],
                geospatial_score=outcome["score"],
            )

    if create_sample_document and not SAMPLE_DOCUMENT.exists():
        _create_sample_document()
    return {
        "inserted": inserted_count,
        "record_count": len(expected_ids),
        "database": str(database_path),
        "sample_document": str(SAMPLE_DOCUMENT),
    }


def _reset_demo_database(*, assume_yes: bool) -> None:
    targets = (
        DEFAULT_DATABASE,
        Path(f"{DEFAULT_DATABASE}-wal"),
        Path(f"{DEFAULT_DATABASE}-shm"),
    )
    existing = [path for path in targets if path.exists()]
    if not existing:
        print(f"No demo database exists at {DEFAULT_DATABASE}.")
        return
    print(
        "WARNING: this will permanently delete the isolated synthetic demo "
        f"database and its audit/validation/alert history: {DEFAULT_DATABASE}"
    )
    if not assume_yes:
        confirmation = input("Type RESET DEMO to continue: ")
        if confirmation != "RESET DEMO":
            print("Reset cancelled; no files were removed.")
            return
    for target in existing:
        target.unlink()
    print("Demo database reset. The application land_records.sqlite3 was not touched.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed or reset the fictional land-record demo.")
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE,
        help="SQLite path for seeding (default: isolated data/demo_land_records.sqlite3).",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Confirm removal of the isolated default demo database before reseeding.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm --reset without an interactive prompt.",
    )
    args = parser.parse_args()
    if args.reset:
        if args.database.resolve() != DEFAULT_DATABASE.resolve():
            parser.error("--reset is allowed only for the isolated default demo database.")
        _reset_demo_database(assume_yes=args.yes)
    try:
        result = seed_demo_database(args.database)
    except (OSError, sqlite3.Error, TypeError, ValueError, RuntimeError) as error:
        print(f"Demo seed failed: {error}", file=sys.stderr)
        return 1
    print(
        f"Demo seed ready: {result['record_count']} synthetic records; "
        f"{result['inserted']} inserted. Database: {result['database']}"
    )
    print(f"Sample document: {result['sample_document']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
