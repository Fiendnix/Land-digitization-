"""Safe document decoding, image preprocessing and Tesseract OCR.

Tesseract's per-word confidence is already expressed on a 0–100 scale.
Only finite, non-negative word confidences are averaged; when none are
available, confidence is ``None`` (never a fabricated 100%).
Set ``TESSERACT_CMD`` to configure a non-PATH Tesseract executable.
"""

from __future__ import annotations

import math
import os
from io import BytesIO
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pymupdf
import pytesseract
from PIL import Image, UnidentifiedImageError

ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".pdf"}
MAX_FILE_SIZE_MB = 20
MAX_PDF_PAGES_PER_RUN = 20
PDF_RENDER_DPI = 300
MAX_IMAGE_PIXELS = 50_000_000
_MAX_FILE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024


class OCRError(Exception):
    """A user-correctable upload or OCR processing error."""


def _configure_tesseract() -> None:
    custom_path = os.environ.get("TESSERACT_CMD", "").strip()
    pytesseract.pytesseract.tesseract_cmd = custom_path or "tesseract"


def is_tesseract_available() -> bool:
    """Check engine availability without hiding unrelated runtime errors."""
    _configure_tesseract()
    try:
        pytesseract.get_tesseract_version()
    except pytesseract.TesseractNotFoundError:
        return False
    except pytesseract.TesseractError:
        return False
    except OSError:
        return False
    return True


def validate_upload(filename: str, data: bytes) -> None:
    """Validate supported extension, nonempty bytes, size, and basic signature."""
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise OCRError(
            f"Unsupported file type '{suffix or '(none)'}'. "
            "Upload a PNG, JPG, JPEG, or PDF."
        )
    if not isinstance(data, bytes):
        raise TypeError("Document data must be bytes")
    if not data:
        raise OCRError("The uploaded file is empty.")
    if len(data) > _MAX_FILE_BYTES:
        raise OCRError(
            f"File is too large ({len(data) / 1024 / 1024:.1f} MB). "
            f"The maximum upload size is {MAX_FILE_SIZE_MB} MB."
        )
    if suffix == ".pdf" and not data.lstrip().startswith(b"%PDF-"):
        raise OCRError("The file does not have a valid PDF signature.")
    if suffix == ".png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise OCRError("The file extension is PNG, but its contents are not a PNG image.")
    if suffix in {".jpg", ".jpeg"} and not data.startswith(b"\xff\xd8\xff"):
        raise OCRError("The file extension is JPG, but its contents are not a JPEG image.")


def get_pdf_page_count(filename: str, data: bytes) -> int:
    """Return the page count for a validated PDF upload."""
    validate_upload(filename, data)
    if Path(filename).suffix.lower() != ".pdf":
        raise OCRError("Page count is only available for PDF documents.")
    try:
        with pymupdf.open(stream=data, filetype="pdf") as document:
            if document.needs_pass:
                raise OCRError("This PDF is password-protected and cannot be processed.")
            if document.page_count == 0:
                raise OCRError("The PDF contains no pages.")
            return document.page_count
    except OCRError:
        raise
    except (pymupdf.FileDataError, ValueError) as error:
        raise OCRError(f"Cannot read this PDF: {error}") from error


def load_images_from_bytes(
    filename: str,
    data: bytes,
    *,
    pdf_pages: list[int] | None = None,
) -> list[Image.Image]:
    """Decode an image upload or render selected zero-based PDF pages.

    PDFs default to their first page. At most 20 pages can be rendered in a
    single call to limit memory use. Returned images are RGB originals used
    for both the preview and OCR preprocessing input.
    """
    validate_upload(filename, data)
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return _load_pdf_pages(data, pdf_pages)
    return [_load_image(data, suffix)]


def _load_image(data: bytes, suffix: str) -> Image.Image:
    try:
        with Image.open(BytesIO(data)) as image:
            expected_format = "PNG" if suffix == ".png" else "JPEG"
            if image.format != expected_format:
                raise OCRError(
                    f"File contents are {image.format or 'unknown'}, not {expected_format}."
                )
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise OCRError(
                    f"Image dimensions are too large to process safely "
                    f"(maximum {MAX_IMAGE_PIXELS:,} pixels)."
                )
            image.load()
            return image.convert("RGB")
    except OCRError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise OCRError(f"Cannot decode this image: {error}") from error


def _load_pdf_pages(data: bytes, pages: list[int] | None) -> list[Image.Image]:
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except (pymupdf.FileDataError, ValueError) as error:
        raise OCRError(f"Cannot open this PDF: {error}") from error

    with document:
        if document.needs_pass:
            raise OCRError("This PDF is password-protected and cannot be processed.")
        if document.page_count == 0:
            raise OCRError("The PDF contains no pages.")
        page_indices = [0] if pages is None else pages
        if not page_indices:
            raise OCRError("Select at least one PDF page to process.")
        if len(page_indices) > MAX_PDF_PAGES_PER_RUN:
            raise OCRError(
                f"Select no more than {MAX_PDF_PAGES_PER_RUN} pages per OCR run."
            )
        if any(
            isinstance(index, bool) or not isinstance(index, int)
            or index < 0 or index >= document.page_count
            for index in page_indices
        ):
            raise OCRError(
                f"Selected page is outside the document's 1–{document.page_count} page range."
            )

        scale = PDF_RENDER_DPI / 72
        matrix = pymupdf.Matrix(scale, scale)
        images: list[Image.Image] = []
        for index in page_indices:
            try:
                page = document.load_page(index)
                page_rect = page.rect
                if (
                    page_rect.width * scale * page_rect.height * scale
                    > MAX_IMAGE_PIXELS
                ):
                    raise OCRError(
                        f"PDF page {index + 1} is too large to render safely."
                    )
                pixmap = page.get_pixmap(
                    matrix=matrix, colorspace=pymupdf.csRGB, alpha=False
                )
                images.append(
                    Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                )
            except OCRError:
                raise
            except (pymupdf.FileDataError, ValueError, RuntimeError) as error:
                raise OCRError(f"Could not render PDF page {index + 1}: {error}") from error
        return images


def preprocess_image(
    image: Image.Image,
    *,
    grayscale: bool = True,
    denoise: bool = True,
    adaptive_threshold: bool = False,
) -> Image.Image:
    """Apply optional grayscale, denoising, and adaptive-threshold steps."""
    pixels = np.asarray(image.convert("RGB"))
    if grayscale:
        pixels = cv2.cvtColor(pixels, cv2.COLOR_RGB2GRAY)
    if denoise:
        if pixels.ndim == 2:
            pixels = cv2.fastNlMeansDenoising(pixels, h=10)
        else:
            pixels = cv2.fastNlMeansDenoisingColored(
                pixels, h=10, hForColoredComponents=10
            )
    if adaptive_threshold:
        if pixels.ndim == 3:
            pixels = cv2.cvtColor(pixels, cv2.COLOR_RGB2GRAY)
        pixels = cv2.adaptiveThreshold(
            pixels, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 15, 8,
        )
    return Image.fromarray(pixels)


def _word_confidence(data: dict[str, Any]) -> float | None:
    confidences: list[float] = []
    for raw_value in data.get("conf", []):
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and 0 <= value <= 100:
            confidences.append(value)
    if not confidences:
        return None
    return round(sum(confidences) / len(confidences), 2)


def run_ocr(
    image: Image.Image,
    *,
    lang: str = "eng",
    preprocess: bool = True,
    grayscale: bool = True,
    denoise: bool = True,
    adaptive_threshold: bool = False,
) -> dict[str, Any]:
    """OCR one page; preserve line breaks and return native 0–100 confidence."""
    if not isinstance(lang, str) or not lang.strip():
        raise OCRError("Enter at least one installed Tesseract language code.")
    _configure_tesseract()
    target = preprocess_image(
        image,
        grayscale=grayscale,
        denoise=denoise,
        adaptive_threshold=adaptive_threshold,
    ) if preprocess else image
    try:
        text = pytesseract.image_to_string(target, lang=lang.strip())
        data = pytesseract.image_to_data(
            target, lang=lang.strip(), output_type=pytesseract.Output.DICT
        )
    except pytesseract.TesseractNotFoundError as error:
        raise OCRError(
            "Tesseract was not found. Install it separately (macOS: `brew install tesseract`) "
            "or set TESSERACT_CMD to its executable path."
        ) from error
    except pytesseract.TesseractError as error:
        raise OCRError(
            f"Tesseract could not process this document: {error}. "
            "Check that the selected language data is installed."
        ) from error
    except OSError as error:
        raise OCRError(f"Could not run Tesseract: {error}") from error

    words = []
    for index, word in enumerate(data.get("text", [])):
        word = word.strip()
        if not word:
            continue
        try:
            confidence = float(data.get("conf", [])[index])
        except (IndexError, TypeError, ValueError):
            confidence = None
        item: dict[str, Any] = {"text": word}
        if confidence is not None and math.isfinite(confidence) and 0 <= confidence <= 100:
            item["confidence"] = confidence
        words.append(item)

    return {
        "text": text,
        "confidence": _word_confidence(data),
        "word_count": len(words),
        "detail": words,
    }


def process_document(
    filename: str,
    data: bytes,
    *,
    lang: str = "eng",
    pdf_pages: list[int] | None = None,
    preprocess: bool = True,
    grayscale: bool = True,
    denoise: bool = True,
    adaptive_threshold: bool = False,
) -> dict[str, Any]:
    """Decode and OCR selected pages, preserving page previews and raw text."""
    images = load_images_from_bytes(filename, data, pdf_pages=pdf_pages)
    page_results = [
        run_ocr(
            image,
            lang=lang,
            preprocess=preprocess,
            grayscale=grayscale,
            denoise=denoise,
            adaptive_threshold=adaptive_threshold,
        )
        for image in images
    ]
    confidences = [
        page["confidence"]
        for page in page_results
        if page["confidence"] is not None
    ]
    return {
        "pages": page_results,
        "images": images,
        "combined_text": "\n\n".join(page["text"] for page in page_results),
        "mean_confidence": (
            round(sum(confidences) / len(confidences), 2) if confidences else None
        ),
    }
