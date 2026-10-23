"""Turn uploads (phone photos, scanner PDFs) into normalized JPEG pages."""
from __future__ import annotations

import io

import pymupdf
from PIL import Image, ImageOps

from app import config

IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


class UnsupportedFile(Exception):
    pass


def normalize_image(data: bytes, max_side: int = config.MAX_IMAGE_SIDE) -> bytes:
    """Fix phone rotation (EXIF), convert to RGB, shrink to max_side, return JPEG bytes."""
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
    except Exception as e:  # PIL raises many different error types for bad files
        raise UnsupportedFile("Không đọc được file ảnh.") from e
    img = img.convert("RGB")
    img.thumbnail((max_side, max_side))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=88)
    return out.getvalue()


def pdf_to_images(data: bytes, dpi: int = config.PDF_DPI) -> list[bytes]:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as e:
        raise UnsupportedFile("Không đọc được file PDF.") from e
    pages = []
    with doc:
        for page in doc:
            pix = page.get_pixmap(dpi=dpi)
            pages.append(normalize_image(pix.tobytes("png")))
    return pages


def split_upload(filename: str, data: bytes) -> list[bytes]:
    """Return one normalized JPEG per page."""
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == ".pdf":
        return pdf_to_images(data)
    if ext in IMAGE_TYPES:
        return [normalize_image(data)]
    raise UnsupportedFile(f"Định dạng {ext or '(không rõ)'} chưa hỗ trợ. Dùng JPG, PNG hoặc PDF.")
