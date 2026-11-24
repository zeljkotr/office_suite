from pathlib import Path
from io import BytesIO

import pytesseract
from PIL import Image, ImageFilter, ImageOps
from pdf2image import convert_from_bytes


SUPPORTED_IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
MAX_PDF_PAGES = 30


class OCRProcessingError(Exception):
    pass


def _extract_image_text(image: Image.Image) -> str:
    image = ImageOps.exif_transpose(image)
    gray = ImageOps.grayscale(image)
    baseline = pytesseract.image_to_string(
        gray, lang="eng", config="--oem 3 --psm 3", timeout=60,
    )

    if len(baseline.strip()) >= 120:
        return baseline

    # Try a lightly denoised Otsu variant, but keep it only when it yields
    # clearly more text so preprocessing cannot replace a better readable scan.
    denoised = gray.filter(ImageFilter.MedianFilter(size=3))
    histogram = denoised.histogram()[:256]
    total = sum(histogram)
    sum_total = sum(index * count for index, count in enumerate(histogram))
    sum_background = weight_background = threshold = 0
    best_variance = -1.0
    for index, count in enumerate(histogram):
        weight_background += count
        if not weight_background:
            continue
        weight_foreground = total - weight_background
        if not weight_foreground:
            break
        sum_background += index * count
        mean_background = sum_background / weight_background
        mean_foreground = (sum_total - sum_background) / weight_foreground
        variance = weight_background * weight_foreground * (mean_background - mean_foreground) ** 2
        if variance > best_variance:
            best_variance, threshold = variance, index
    enhanced = denoised.point(lambda pixel: 255 if pixel > threshold else 0)
    candidate = pytesseract.image_to_string(
        enhanced, lang="eng", config="--oem 3 --psm 3", timeout=60,
    )
    return candidate if len(candidate.strip()) > len(baseline.strip()) * 1.15 else baseline


def extract_text(file_bytes: bytes, filename: str) -> str:
    if not file_bytes:
        raise OCRProcessingError("The uploaded document is empty.")

    extension = Path(filename).suffix.lower()

    try:
        if extension == ".pdf":
            pages = convert_from_bytes(
                file_bytes,
                dpi=250,
                first_page=1,
                last_page=MAX_PDF_PAGES,
            )

            if not pages:
                raise OCRProcessingError(
                    "No readable pages were found in the PDF."
                )

            extracted_pages = []

            for page_number, page in enumerate(pages, start=1):
                text = _extract_image_text(page)
                extracted_pages.append(
                    f"--- Page {page_number} ---\n{text}"
                )

            return "\n\n".join(extracted_pages).strip()

        if extension in SUPPORTED_IMAGE_TYPES:
            with Image.open(BytesIO(file_bytes)) as image:
                return _extract_image_text(image).strip()

        raise OCRProcessingError(
            "Unsupported file type. Upload PDF, JPG, PNG or TIFF."
        )

    except OCRProcessingError:
        raise
    except Exception as exc:
        raise OCRProcessingError(
            f"Document OCR processing failed: {type(exc).__name__}."
        ) from exc
