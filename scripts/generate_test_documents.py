"""Generate rough, explicitly fictional scanned PDFs for OCR testing."""

from __future__ import annotations

import random
from io import BytesIO
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "data" / "demo" / "test_documents"
PAGE_SIZE = (1275, 1800)
DISCLAIMER = "SYNTHETIC TEST DOCUMENT - NOT A GOVERNMENT RECORD - NO LEGAL VALIDITY"

DOCUMENTS = (
    (
        "01-complete-fictional-record.pdf",
        "TEST-PARCEL-001",
        "KH-12/3",
        "Anaya Test",
        "Samplepur",
        "Agricultural",
        "1.75 hectares",
        "2021",
        1.2,
        72,
    ),
    (
        "02-missing-owner-duplicate-survey.pdf",
        "TEST-PARCEL-002",
        "KH-12/3",
        None,
        "Samplepur",
        "Agricultural",
        "0.80 hectares",
        "2020",
        -1.4,
        82,
    ),
    (
        "03-invalid-area-low-quality-scan.pdf",
        "TEST-PARCEL-003",
        "KH-18/4",
        "Meera Sample",
        "Exampleganj",
        "Residential",
        "0.00 hectares",
        "2019",
        1.8,
        88,
    ),
)


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/Arial Bold.ttf" if bold else "/Library/Fonts/Arial.ttf",
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _paper_texture(seed: int, stain_level: int) -> Image.Image:
    width, height = PAGE_SIZE
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width]
    edge_distance = np.minimum.reduce((xx, width - 1 - xx, yy, height - 1 - yy))
    edge_shade = np.clip((72 - edge_distance) / 96, 0, 1) * 49
    lighting = 8 * np.sin(xx / 210) + 5 * np.cos(yy / 260)
    grain = rng.normal(0, 3.8 + stain_level * 0.08, (height, width))
    base = np.clip(238 - edge_shade - lighting - grain, 0, 255)
    rgb = np.stack(
        (base, np.clip(base - 4, 0, 255), np.clip(base - 15, 0, 255)),
        axis=-1,
    ).astype(np.uint8)
    image = Image.fromarray(rgb, mode="RGB")

    stains = Image.new("RGBA", PAGE_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(stains)
    stain_rng = random.Random(seed + 300)
    for _ in range(stain_level):
        x = stain_rng.randint(35, width - 35)
        y = stain_rng.randint(35, height - 35)
        rx = stain_rng.randint(8, 75)
        ry = stain_rng.randint(5, 48)
        alpha = stain_rng.randint(15, 42)
        draw.ellipse((x - rx, y - ry, x + rx, y + ry), fill=(111, 79, 42, alpha))
    image = Image.alpha_composite(image.convert("RGBA"), stains).convert("RGB")

    # Fine paper fibers and scan dust, deterministic for repeatable fixtures.
    dust = Image.new("RGBA", PAGE_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(dust)
    for _ in range(2200 + stain_level * 24):
        x = stain_rng.randrange(width)
        y = stain_rng.randrange(height)
        alpha = stain_rng.randrange(10, 50)
        shade = stain_rng.randrange(70, 145)
        draw.point((x, y), fill=(shade, shade - 8, shade - 20, alpha))
    return Image.alpha_composite(image.convert("RGBA"), dust).convert("RGB")


def _draw_form(record: tuple[object, ...], seed: int) -> Image.Image:
    filename, parcel, survey, owner, village, land_use, area, year, skew, roughness = record
    image = _paper_texture(seed, int(roughness))
    draw = ImageDraw.Draw(image)
    title = _font(39, bold=True)
    section = _font(27, bold=True)
    body = _font(31)
    small = _font(24)

    # Uneven, slightly faded rules imitate a handled photocopy without copying
    # any real agency's letterhead, seals, logos, or signatures.
    draw.rectangle((65, 65, 1208, 1735), outline=(76, 69, 56), width=3)
    draw.rectangle((84, 82, 1188, 278), outline=(112, 101, 81), width=2)
    draw.text((112, 107), "FICTIONAL LAND RECORD - OCR TEST SHEET", font=title, fill=(52, 60, 60))
    draw.text((112, 177), DISCLAIMER, font=small, fill=(142, 48, 43))
    draw.line((104, 310, 1167, 306), fill=(97, 87, 68), width=3)
    draw.text((112, 335), "SAMPLE REGISTER EXTRACT / KHEWAT-STYLE FIELD LAYOUT", font=section, fill=(65, 70, 67))
    draw.text((112, 384), "All names, locations and identifiers on this sheet are invented.", font=small, fill=(91, 87, 76))

    rows: list[tuple[str, str]] = [
        ("Parcel ID", str(parcel)),
        ("Khasra No", str(survey)),
    ]
    if owner is not None:
        rows.append(("Owner Name", str(owner)))
    else:
        rows.append(("Owner Name", "[INTENTIONALLY LEFT BLANK FOR TESTING]"))
    rows.extend(
        [
            ("District", "Demo Pradesh"),
            ("Tehsil", "Fictional Tehsil"),
            ("Village", str(village)),
            ("Land Use", str(land_use)),
            ("Area", str(area)),
            ("Document Year", str(year)),
        ]
    )
    table_top = 466
    row_height = 115
    label_x = 112
    value_x = 488
    table_right = 1165
    draw.line((102, table_top, table_right, table_top - 2), fill=(106, 94, 73), width=3)
    for index, (label, value) in enumerate(rows):
        top = table_top + index * row_height
        # Slight alternation helps the form feel photocopied rather than pristine.
        ink = (62 + index % 3 * 5, 65 + index % 2 * 4, 61)
        draw.text((label_x, top + 27), f"{label}:", font=body, fill=ink)
        draw.text((value_x, top + 27), value, font=body, fill=ink)
        draw.line(
            (102, top + row_height - 3, table_right - (index % 3) * 3, top + row_height),
            fill=(143, 132, 109),
            width=2,
        )
    footer = table_top + len(rows) * row_height + 45
    draw.text((110, footer), "For OCR software testing only. Not evidence of ownership.", font=small, fill=(82, 78, 67))
    draw.text((110, footer + 48), "NOT FOR SUBMISSION - NO OFFICIAL SEAL OR CERTIFICATION", font=small, fill=(142, 48, 43))
    draw.text((110, footer + 120), "TEST COPY / FICTIONAL DATA", font=section, fill=(142, 48, 43))

    # Strong diagonal disclaimer remains legible after the scan treatment.
    watermark = Image.new("RGBA", PAGE_SIZE, (0, 0, 0, 0))
    watermark_draw = ImageDraw.Draw(watermark)
    watermark_draw.text((90, 1080), "SYNTHETIC - NOT VALID", font=_font(62, bold=True), fill=(149, 44, 40, 45))
    watermark = watermark.rotate(17, resample=Image.Resampling.BICUBIC, center=(PAGE_SIZE[0] // 2, PAGE_SIZE[1] // 2))
    image = Image.alpha_composite(image.convert("RGBA"), watermark).convert("RGB")

    # Fold lines, staple shadows, and worn corners are scan artifacts, not official marks.
    artifacts = Image.new("RGBA", PAGE_SIZE, (0, 0, 0, 0))
    artifact_draw = ImageDraw.Draw(artifacts)
    fold_y = 890 + seed % 90
    artifact_draw.line((65, fold_y, 1210, fold_y - 3), fill=(73, 68, 57, 28), width=12)
    artifact_draw.line((65, fold_y + 13, 1210, fold_y + 10), fill=(255, 249, 224, 80), width=8)
    artifact_draw.arc((1050, 80, 1250, 280), 85, 185, fill=(83, 75, 61, 80), width=5)
    for offset in range(3):
        artifact_draw.line((112 + offset * 2, 78, 164 + offset * 2, 78), fill=(55, 55, 53, 110), width=2)
    artifact_draw.line((640, 320, 626, 1660), fill=(75, 68, 57, 30), width=8)
    artifact_draw.line((651, 320, 640, 1660), fill=(255, 249, 224, 52), width=5)
    image = Image.alpha_composite(image.convert("RGBA"), artifacts).convert("RGB")

    image = image.rotate(float(skew), resample=Image.Resampling.BICUBIC, expand=False, fillcolor=(224, 214, 193))
    if int(roughness) > 90:
        reduced = image.resize((760, 1073), Image.Resampling.BILINEAR)
        image = reduced.resize(PAGE_SIZE, Image.Resampling.BICUBIC)
        image = image.filter(ImageFilter.GaussianBlur(radius=0.85))
        image = ImageEnhance.Contrast(image).enhance(0.76)
    elif int(roughness) > 75:
        reduced = image.resize((900, 1270), Image.Resampling.BILINEAR)
        image = reduced.resize(PAGE_SIZE, Image.Resampling.BICUBIC)
        image = image.filter(ImageFilter.GaussianBlur(radius=0.55))
        image = ImageEnhance.Contrast(image).enhance(0.82)
    return image


def _save_pdf(path: Path, image: Image.Image, quality: int) -> None:
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=quality, optimize=True)
    pdf = pymupdf.open()
    page = pdf.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=buffer.getvalue())
    pdf.set_metadata(
        {
            "title": f"Fictional OCR test sheet - {path.stem}",
            "author": "Synthetic demo data generator",
            "subject": "Not a government record; no legal validity",
            "keywords": "synthetic, OCR test, fictional",
            "creator": "scripts/generate_test_documents.py",
            "producer": "PyMuPDF",
            "creationDate": "D:20260101000000Z",
            "modDate": "D:20260101000000Z",
        }
    )
    pdf.save(path, deflate=True, reproducible=True)
    pdf.close()


def generate() -> list[Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    paths = []
    for index, record in enumerate(DOCUMENTS, start=1):
        filename, *_rest, quality = record
        image = _draw_form(record, seed=26018 + index)
        path = OUTPUT_DIR / str(filename)
        _save_pdf(path, image, quality=max(30, int(96 - (int(quality) - 50) * 0.6)))
        paths.append(path)
    return paths


if __name__ == "__main__":
    for generated_path in generate():
        print(f"Generated {generated_path} ({generated_path.stat().st_size:,} bytes)")
