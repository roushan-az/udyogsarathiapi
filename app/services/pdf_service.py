# app/services/pdf_service.py
"""
Image → PDF conversion — entirely in memory, no temp files on disk.

Strategy:
  1. Validate and open the image with Pillow.
  2. Correct EXIF rotation so PDFs are right-side-up.
  3. Use img2pdf for fast, lossless conversion (preserves JPEG data verbatim).
  4. Fall back to Pillow/ReportLab if img2pdf fails (e.g. TIFF, BMP).
  5. Return (pdf_bytes, page_count) — caller owns the bytes.

The FastAPI route calls this and immediately streams the result to Azure Blob.
No bytes are ever written to the filesystem.
"""

import io
from typing import Tuple

import img2pdf
from PIL import Image, ImageOps

from app.core.config import settings
from app.core.exceptions import PDFConversionError
from app.core.logging import get_logger

logger = get_logger(__name__)

# img2pdf layout — A4 at the configured DPI
_A4_LAYOUT = img2pdf.get_layout_fun(
    pagesize=(img2pdf.mm_to_pt(210), img2pdf.mm_to_pt(297))
)


def _correct_exif_rotation(img: Image.Image) -> Image.Image:
    """Apply EXIF orientation tag so photos taken on phones appear upright."""
    return ImageOps.exif_transpose(img)


def _validate_image(image_bytes: bytes, content_type: str) -> Image.Image:
    """
    Open and validate the image.  Raises PDFConversionError on malformed data.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.verify()  # detect truncated / corrupt files
        # Re-open after verify() (PIL quirk — file pointer is exhausted after verify)
        img = Image.open(io.BytesIO(image_bytes))
        img = _correct_exif_rotation(img)
        return img
    except Exception as exc:
        raise PDFConversionError(
            detail=f"Could not open image: {exc}",
            content_type=content_type,
        )


def _convert_via_img2pdf(image_bytes: bytes) -> bytes:
    """
    Fast path: use img2pdf to embed JPEG/PNG data without re-encoding.
    Returns raw PDF bytes.
    """
    buf = io.BytesIO()
    buf.write(img2pdf.convert(image_bytes, layout_fun=_A4_LAYOUT))
    return buf.getvalue()


def _convert_via_pillow(img: Image.Image) -> bytes:
    """
    Fallback path (TIFF, BMP, WEBP, etc.):
    Convert the image to JPEG in memory then wrap in a PDF with ReportLab.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas as rl_canvas

    # Convert to RGB (strip alpha, handle palette modes)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    # Encode image as JPEG in memory
    jpeg_buf = io.BytesIO()
    img.save(jpeg_buf, format="JPEG", quality=settings.PDF_IMAGE_QUALITY)
    jpeg_buf.seek(0)

    # Build A4 PDF with ReportLab
    pdf_buf = io.BytesIO()
    page_w, page_h = A4  # 595.27 × 841.89 points

    c = rl_canvas.Canvas(pdf_buf, pagesize=A4)

    # Scale image to fit the page while preserving aspect ratio
    img_w, img_h = img.size
    ratio = min(page_w / img_w, page_h / img_h)
    draw_w = img_w * ratio
    draw_h = img_h * ratio
    x_off = (page_w - draw_w) / 2
    y_off = (page_h - draw_h) / 2

    c.drawImage(ImageReader(jpeg_buf), x_off, y_off, draw_w, draw_h)
    c.showPage()
    c.save()

    return pdf_buf.getvalue()


def convert_image_to_pdf(
    image_bytes: bytes,
    content_type: str,
) -> Tuple[bytes, int]:
    """
    Public entry point.

    Args:
        image_bytes:  Raw bytes of the uploaded image.
        content_type: MIME type (e.g. "image/jpeg").

    Returns:
        (pdf_bytes, page_count) — page_count is always 1 for single-image upload.

    Raises:
        PDFConversionError: If the image cannot be processed.
    """
    logger.info("pdf_conversion_start", content_type=content_type, size=len(image_bytes))

    # Validate first
    img = _validate_image(image_bytes, content_type)

    try:
        # Fast path — img2pdf handles JPEG and PNG natively
        if content_type in ("image/jpeg", "image/jpg", "image/png"):
            pdf_bytes = _convert_via_img2pdf(image_bytes)
        else:
            # Fallback for WEBP, TIFF, BMP
            pdf_bytes = _convert_via_pillow(img)

        page_count = 1
        logger.info(
            "pdf_conversion_success",
            pdf_size=len(pdf_bytes),
            page_count=page_count,
        )
        return pdf_bytes, page_count

    except PDFConversionError:
        raise
    except Exception as exc:
        logger.exception("pdf_conversion_failed", error=str(exc))
        raise PDFConversionError(
            detail=f"PDF generation failed: {exc}",
            content_type=content_type,
        )