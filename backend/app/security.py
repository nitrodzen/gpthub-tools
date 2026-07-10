from __future__ import annotations

import asyncio
import zipfile
from pathlib import Path

import clamd
import pikepdf
from PIL import Image
from pillow_heif import register_heif_opener

from .config import settings
from .models import ErrorCode, JobFailure, Operation

register_heif_opener()
Image.MAX_IMAGE_PIXELS = settings.max_image_pixels

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".heic", ".heif", ".tif", ".tiff", ".bmp"}
DOCUMENT_EXTENSIONS = {".doc", ".docx", ".odt", ".rtf", ".pdf"}
PDF_ONLY = {".pdf"}


def allowed_extensions(operation: Operation) -> set[str]:
    if operation in {
        Operation.UPSCALE,
        Operation.REMOVE_BACKGROUND,
        Operation.IMAGE_CONVERT,
        Operation.IMAGES_TO_PDF,
    }:
        return IMAGE_EXTENSIONS
    if operation is Operation.DOCUMENT_CONVERT:
        return DOCUMENT_EXTENSIONS
    if operation in {Operation.PDF_MERGE, Operation.PDF_SPLIT, Operation.PDF_TO_IMAGES}:
        return PDF_ONLY
    return set()


def validate_signature(path: Path, extension: str) -> None:
    with path.open("rb") as source:
        head = source.read(16)
    if extension == ".pdf":
        if not head.startswith(b"%PDF-"):
            raise JobFailure(ErrorCode.INVALID_FILE, "The file is not a valid PDF")
        try:
            with pikepdf.open(path) as document:
                if document.is_encrypted:
                    raise JobFailure(
                        ErrorCode.PDF_PASSWORD_PROTECTED,
                        "Password-protected PDFs are not supported",
                    )
                if len(document.pages) > settings.max_pdf_pages:
                    raise JobFailure(
                        ErrorCode.PDF_TOO_MANY_PAGES, "The PDF contains too many pages"
                    )
        except pikepdf.PasswordError as exc:
            raise JobFailure(
                ErrorCode.PDF_PASSWORD_PROTECTED, "Password-protected PDFs are not supported"
            ) from exc
        except pikepdf.PdfError as exc:
            raise JobFailure(ErrorCode.INVALID_FILE, "The PDF is damaged or invalid") from exc
        return

    if extension in IMAGE_EXTENSIONS:
        try:
            with Image.open(path) as image:
                width, height = image.size
                if width * height > settings.max_image_pixels:
                    raise JobFailure(
                        ErrorCode.IMAGE_TOO_LARGE, "The image dimensions are too large"
                    )
                image.verify()
        except JobFailure:
            raise
        except Exception as exc:
            raise JobFailure(ErrorCode.INVALID_FILE, "The image is damaged or unsupported") from exc
        return

    if extension in {".docx", ".odt"}:
        if not zipfile.is_zipfile(path):
            raise JobFailure(ErrorCode.INVALID_FILE, "The document container is invalid")
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            marker = "word/document.xml" if extension == ".docx" else "mimetype"
            if marker not in names:
                raise JobFailure(ErrorCode.INVALID_FILE, "The document structure is invalid")
        return

    if extension == ".doc" and not head.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
        raise JobFailure(ErrorCode.INVALID_FILE, "The legacy Word document is invalid")
    if extension == ".rtf" and not head.lstrip().startswith(b"{\\rtf"):
        raise JobFailure(ErrorCode.INVALID_FILE, "The RTF document is invalid")


def _scan_sync(path: Path) -> None:
    try:
        scanner = clamd.ClamdNetworkSocket(settings.clamav_host, settings.clamav_port, timeout=30)
        with path.open("rb") as source:
            result = scanner.instream(source)
        if result:
            status = result.get("stream", next(iter(result.values())))[0]
            if status == "FOUND":
                raise JobFailure(
                    ErrorCode.MALWARE_DETECTED, "The uploaded file failed the malware scan"
                )
    except JobFailure:
        raise
    except Exception as exc:
        if settings.clamav_required:
            raise JobFailure(
                ErrorCode.SCANNER_UNAVAILABLE, "The malware scanner is temporarily unavailable"
            ) from exc


async def scan_file(path: Path) -> None:
    await asyncio.to_thread(_scan_sync, path)
