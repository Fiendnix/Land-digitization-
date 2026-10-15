from io import BytesIO

import pymupdf
import pytest
from PIL import Image

from modules import ocr
from modules.ocr import OCRError, load_images_from_bytes, process_document, run_ocr


def png_bytes(color="white"):
    image = Image.new("RGB", (160, 80), color=color)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def pdf_bytes():
    document = pymupdf.open()
    page = document.new_page(width=180, height=100)
    page.insert_text((20, 50), "Scanned land record", fontsize=12)
    data = document.tobytes()
    document.close()
    return data


def test_loads_valid_png():
    images = load_images_from_bytes("record.png", png_bytes())

    assert len(images) == 1
    assert images[0].mode == "RGB"
    assert images[0].size == (160, 80)


def test_rejects_unsupported_extension_and_bad_signature():
    with pytest.raises(OCRError, match="Upload a PNG"):
        load_images_from_bytes("record.tiff", b"not an image")
    with pytest.raises(OCRError, match="valid PDF signature"):
        load_images_from_bytes("record.pdf", b"not a pdf")


def test_rejects_mismatched_extension():
    with pytest.raises(OCRError, match="not a JPEG"):
        load_images_from_bytes("record.jpg", png_bytes())


def test_rejects_empty_and_oversized_upload(monkeypatch):
    with pytest.raises(OCRError, match="empty"):
        load_images_from_bytes("empty.png", b"")

    monkeypatch.setattr(ocr, "_MAX_FILE_BYTES", 4)
    with pytest.raises(OCRError, match="too large"):
        load_images_from_bytes("large.png", png_bytes())


def test_renders_pdf_first_and_selected_pages():
    data = pdf_bytes()
    first = load_images_from_bytes("record.pdf", data)
    selected = load_images_from_bytes("record.pdf", data, pdf_pages=[0])

    assert len(first) == len(selected) == 1
    assert first[0].size == selected[0].size
    assert first[0].size[0] > 180


def test_rejects_out_of_range_pdf_page():
    with pytest.raises(OCRError, match="outside"):
        load_images_from_bytes("record.pdf", pdf_bytes(), pdf_pages=[4])


def test_preprocessing_returns_image_without_mutating_source():
    image = Image.new("RGB", (24, 24), color="white")
    result = ocr.preprocess_image(
        image, grayscale=True, denoise=True, adaptive_threshold=True
    )

    assert result.mode == "L"
    assert image.mode == "RGB"


def test_custom_tesseract_path_is_configurable(monkeypatch):
    monkeypatch.setattr(ocr.pytesseract.pytesseract, "tesseract_cmd", "tesseract")
    monkeypatch.setenv("TESSERACT_CMD", "/opt/custom/bin/tesseract")

    ocr._configure_tesseract()

    assert ocr.pytesseract.pytesseract.tesseract_cmd == "/opt/custom/bin/tesseract"


def test_missing_tesseract_is_reported_as_helpful_ocr_error(monkeypatch):
    def raise_missing_engine(*args, **kwargs):
        raise ocr.pytesseract.TesseractNotFoundError()

    monkeypatch.delenv("TESSERACT_CMD", raising=False)
    monkeypatch.setattr(ocr.pytesseract, "image_to_string", raise_missing_engine)

    with pytest.raises(OCRError, match="Tesseract was not found"):
        run_ocr(Image.new("RGB", (10, 10)), preprocess=False)


def test_ocr_preserves_raw_text_and_averages_valid_engine_confidence(monkeypatch):
    monkeypatch.setattr(ocr.pytesseract, "image_to_string", lambda *a, **k: "Line one\nLine two\n")
    monkeypatch.setattr(
        ocr.pytesseract,
        "image_to_data",
        lambda *a, **k: {
            "text": ["Line", "one", "Line", "two", ""],
            "conf": ["80", "100", "60", "-1", "-1"],
        },
    )

    result = run_ocr(Image.new("RGB", (10, 10)), preprocess=False)

    assert result["text"] == "Line one\nLine two\n"
    assert result["confidence"] == 80.0
    assert result["word_count"] == 4


def test_missing_engine_confidence_is_none(monkeypatch):
    monkeypatch.setattr(ocr.pytesseract, "image_to_string", lambda *a, **k: "recognized")
    monkeypatch.setattr(
        ocr.pytesseract,
        "image_to_data",
        lambda *a, **k: {"text": ["recognized"], "conf": ["-1"]},
    )

    result = run_ocr(Image.new("RGB", (10, 10)), preprocess=False)

    assert result["confidence"] is None


def test_process_document_returns_preview_raw_text_and_confidence(monkeypatch):
    monkeypatch.setattr(
        ocr,
        "run_ocr",
        lambda image, **kwargs: {
            "text": "Parcel ID: P-1\n",
            "confidence": 73.5,
            "word_count": 3,
            "detail": [],
        },
    )

    result = process_document("record.png", png_bytes())

    assert result["combined_text"] == "Parcel ID: P-1\n"
    assert result["mean_confidence"] == 73.5
    assert len(result["images"]) == len(result["pages"]) == 1
