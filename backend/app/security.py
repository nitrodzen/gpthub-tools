from __future__ import annotations

import asyncio
import zipfile
from pathlib import Path, PurePosixPath

import clamd
import msoffcrypto
import olefile
import pikepdf
from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException
from msoffcrypto.exceptions import FileFormatError
from oletools.olevba import VBA_Parser
from PIL import Image
from pillow_heif import register_heif_opener

from .config import settings
from .models import ErrorCode, JobFailure, Operation

register_heif_opener()
Image.MAX_IMAGE_PIXELS = settings.max_image_pixels

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".heic", ".heif", ".tif", ".tiff", ".bmp"}
DOCUMENT_EXTENSIONS = {".doc", ".docx", ".odt", ".rtf", ".pdf"}
WORD_EXTENSIONS = {".doc", ".docx", ".odt", ".rtf"}
SPREADSHEET_EXTENSIONS = {".xls", ".xlsx", ".ods", ".csv"}
PDF_ONLY = {".pdf"}
MAX_UPSCALE_OUTPUT_PIXELS = settings.max_upscale_output_pixels
MAX_OFFICE_ARCHIVE_ENTRIES = 10_000
MAX_OFFICE_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_OFFICE_COMPRESSION_RATIO = 250
MAX_OFFICE_MEDIA_BYTES = 100 * 1024 * 1024
MAX_OFFICE_MEDIA_FILES = 50
MAX_OFFICE_METADATA_BYTES = 16 * 1024 * 1024
MAX_OFFICE_XML_ELEMENTS = 1_500_000
MAX_ODF_XML_MEMBER_BYTES = 24 * 1024 * 1024
MAX_ODF_XML_TOTAL_BYTES = 48 * 1024 * 1024
MAX_ODF_XML_TEXT_CHARS = 12_000_000
MAX_ODF_XML_ATTRIBUTE_CHARS = 4_000_000
MAX_ODF_XML_DEPTH = 256
MAX_OLE_DIRECTORY_ENTRIES = 10_000

OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
DOCX_MAIN_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)
XLSX_MAIN_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
)

GENERIC_UPLOAD_TYPES = {
    "",
    "application/octet-stream",
    "application/x-ole-storage",
    "binary/octet-stream",
}
OFFICE_MIME_TYPES = {
    ".doc": {"application/msword"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
        "application/zip",
        "application/x-zip-compressed",
    },
    ".odt": {
        "application/vnd.oasis.opendocument.text",
        "application/zip",
        "application/x-zip-compressed",
    },
    ".rtf": {"application/rtf", "application/x-rtf", "text/rtf", "text/plain"},
    ".xls": {"application/vnd.ms-excel"},
    ".xlsx": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
        "application/zip",
        "application/x-zip-compressed",
    },
    ".ods": {
        "application/vnd.oasis.opendocument.spreadsheet",
        "application/zip",
        "application/x-zip-compressed",
    },
    ".csv": {"application/csv", "application/vnd.ms-excel", "text/csv", "text/plain"},
}


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
    if operation is Operation.WORD_TO_EXCEL:
        return WORD_EXTENSIONS
    if operation is Operation.EXCEL_TO_WORD:
        return SPREADSHEET_EXTENSIONS
    if operation in {Operation.PDF_MERGE, Operation.PDF_SPLIT, Operation.PDF_TO_IMAGES}:
        return PDF_ONLY
    return set()


def _office_is_encrypted(path: Path) -> bool:
    try:
        with path.open("rb") as source:
            office = msoffcrypto.OfficeFile(source)
            return bool(office.is_encrypted())
    except FileFormatError:
        return False
    except Exception as exc:
        raise JobFailure(ErrorCode.INVALID_FILE, "The Office document is invalid") from exc


def _office_has_macros(path: Path) -> bool:
    parser = None
    try:
        parser = VBA_Parser(str(path))
        if parser.detect_vba_macros():
            return True
        detect_xlm = getattr(parser, "detect_xlm_macros", None)
        return bool(detect_xlm and detect_xlm())
    except Exception as exc:
        raise JobFailure(
            ErrorCode.INVALID_FILE,
            "The Office file could not be inspected safely for macros",
        ) from exc
    finally:
        if parser is not None:
            try:
                parser.close()
            except Exception as exc:
                raise JobFailure(
                    ErrorCode.INVALID_FILE,
                    "The Office macro inspection could not finish safely",
                ) from exc


def _validate_legacy_office(path: Path, extension: str) -> None:
    try:
        if not olefile.isOleFile(str(path)):
            raise JobFailure(ErrorCode.INVALID_FILE, "The legacy Office document is invalid")
        with olefile.OleFileIO(str(path), raise_defects=olefile.DEFECT_INCORRECT) as compound:
            entries = compound.listdir(streams=True, storages=True)
            if len(entries) > MAX_OLE_DIRECTORY_ENTRIES:
                raise JobFailure(ErrorCode.OFFICE_TOO_COMPLEX, "The Office file is too complex")
            names = {"/".join(entry).casefold() for entry in entries}
            if extension == ".doc" and not any(name.endswith("worddocument") for name in names):
                raise JobFailure(ErrorCode.INVALID_FILE, "The legacy Word document is invalid")
            if extension == ".xls" and not any(
                name.endswith("workbook") or name.endswith("book") for name in names
            ):
                raise JobFailure(ErrorCode.INVALID_FILE, "The legacy Excel workbook is invalid")
    except JobFailure:
        raise
    except (OSError, olefile.OleFileError) as exc:
        raise JobFailure(ErrorCode.INVALID_FILE, "The legacy Office document is invalid") from exc
    if _office_is_encrypted(path):
        raise JobFailure(
            ErrorCode.OFFICE_PASSWORD_PROTECTED,
            "Password-protected Office files are not supported",
        )
    if _office_has_macros(path):
        raise JobFailure(
            ErrorCode.OFFICE_MACROS_NOT_ALLOWED,
            "Office files containing macros are not supported",
        )


def _odf_has_active_content(archive: zipfile.ZipFile, infos: list[zipfile.ZipInfo]) -> bool:
    elements = 0
    xml_bytes = 0
    text_characters = 0
    attribute_characters = 0
    try:
        for info in infos:
            if not info.filename.casefold().endswith(".xml"):
                continue
            if info.file_size > MAX_ODF_XML_MEMBER_BYTES:
                raise JobFailure(
                    ErrorCode.OFFICE_TOO_COMPLEX,
                    "An XML part in the OpenDocument file is too large",
                )
            xml_bytes += info.file_size
            if xml_bytes > MAX_ODF_XML_TOTAL_BYTES:
                raise JobFailure(
                    ErrorCode.OFFICE_TOO_COMPLEX,
                    "The OpenDocument file contains too much XML data",
                )
            depth = 0
            with archive.open(info) as source:
                for event, element in ET.iterparse(source, events=("start", "end")):
                    tag = str(element.tag)
                    local_name = tag.rsplit("}", 1)[-1].casefold()
                    if event == "start":
                        elements += 1
                        depth += 1
                        attribute_characters += sum(
                            len(str(name)) + len(str(value))
                            for name, value in element.attrib.items()
                        )
                        if elements > MAX_OFFICE_XML_ELEMENTS:
                            raise JobFailure(
                                ErrorCode.OFFICE_TOO_COMPLEX,
                                "The Office file contains too much XML structure",
                            )
                        if depth > MAX_ODF_XML_DEPTH:
                            raise JobFailure(
                                ErrorCode.OFFICE_TOO_COMPLEX,
                                "The OpenDocument XML nesting is too deep",
                            )
                        if attribute_characters > MAX_ODF_XML_ATTRIBUTE_CHARS:
                            raise JobFailure(
                                ErrorCode.OFFICE_TOO_COMPLEX,
                                "The OpenDocument file contains too much XML attribute data",
                            )
                        continue
                    text_characters += len(element.text or "") + len(element.tail or "")
                    if text_characters > MAX_ODF_XML_TEXT_CHARS:
                        raise JobFailure(
                            ErrorCode.OFFICE_TOO_COMPLEX,
                            "The OpenDocument file contains too much XML text",
                        )
                    if local_name in {"event-listener", "macro", "script"}:
                        return True
                    if (
                        local_name in {"event-listeners", "scripts"}
                        and (element.text or "").strip()
                    ):
                        return True
                    for raw_name, raw_value in element.attrib.items():
                        name = str(raw_name).casefold()
                        attribute_name = name.rsplit("}", 1)[-1]
                        value = str(raw_value).casefold()
                        if (
                            ("script" in name and attribute_name in {"language", "macro-name"})
                            or attribute_name in {"event-name", "macro-name"}
                            or value.startswith(("macro:", "vnd.sun.star.script:"))
                            or "ooo:basic" in value
                        ):
                            return True
                    element.clear()
                    depth -= 1
    except JobFailure:
        raise
    except (DefusedXmlException, ET.ParseError, OSError) as exc:
        raise JobFailure(ErrorCode.INVALID_FILE, "The document structure is invalid") from exc
    return False


def _validate_zip_office(path: Path, extension: str) -> None:
    if not zipfile.is_zipfile(path):
        with path.open("rb") as source:
            is_ole = source.read(8) == OLE_MAGIC
        if is_ole:
            _validate_legacy_office(path, extension)
        raise JobFailure(ErrorCode.INVALID_FILE, "The document container is invalid")
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_OFFICE_ARCHIVE_ENTRIES:
                raise JobFailure(ErrorCode.OFFICE_TOO_COMPLEX, "The Office file is too complex")
            total_size = 0
            media_size = 0
            media_count = 0
            names: set[str] = set()
            for info in infos:
                normalized = str(PurePosixPath(info.filename.replace("\\", "/")))
                if normalized.startswith("/") or ".." in PurePosixPath(normalized).parts:
                    raise JobFailure(ErrorCode.INVALID_FILE, "The document container is invalid")
                total_size += info.file_size
                if total_size > MAX_OFFICE_UNCOMPRESSED_BYTES:
                    raise JobFailure(ErrorCode.OFFICE_TOO_COMPLEX, "The Office file is too complex")
                if info.file_size and info.compress_size == 0:
                    raise JobFailure(ErrorCode.OFFICE_TOO_COMPLEX, "The Office file is too complex")
                if (
                    info.file_size > 1024 * 1024
                    and info.compress_size > 0
                    and info.file_size / info.compress_size > MAX_OFFICE_COMPRESSION_RATIO
                ):
                    raise JobFailure(ErrorCode.OFFICE_TOO_COMPLEX, "The Office file is too complex")
                lowered = normalized.casefold()
                if lowered in names:
                    raise JobFailure(
                        ErrorCode.INVALID_FILE,
                        "The document container contains duplicate entries",
                    )
                if lowered.startswith(
                    (
                        "word/media/",
                        "xl/media/",
                        "pictures/",
                        "objectreplacements/",
                    )
                ):
                    media_count += 1
                    media_size += info.file_size
                    if media_count > MAX_OFFICE_MEDIA_FILES:
                        raise JobFailure(
                            ErrorCode.OFFICE_TOO_COMPLEX,
                            "The Office file contains too many embedded media files",
                        )
                    if media_size > MAX_OFFICE_MEDIA_BYTES:
                        raise JobFailure(
                            ErrorCode.OFFICE_TOO_COMPLEX,
                            "The Office file contains too much embedded media",
                        )
                names.add(lowered)

            macro_entry = any(
                name.endswith("vbaproject.bin")
                or name.startswith("basic/")
                or name.startswith("scripts/")
                or "/macrosheets/" in name
                for name in names
            )
            if macro_entry:
                raise JobFailure(
                    ErrorCode.OFFICE_MACROS_NOT_ALLOWED,
                    "Office files containing macros are not supported",
                )

            if extension in {".docx", ".xlsx"}:
                marker = "word/document.xml" if extension == ".docx" else "xl/workbook.xml"
                if marker not in names or "[content_types].xml" not in names:
                    raise JobFailure(ErrorCode.INVALID_FILE, "The document structure is invalid")
                content_info = next(
                    info for info in infos if info.filename.casefold() == "[content_types].xml"
                )
                if content_info.file_size > MAX_OFFICE_METADATA_BYTES:
                    raise JobFailure(ErrorCode.OFFICE_TOO_COMPLEX, "The Office file is too complex")
                try:
                    macro_content_type = False
                    expected_part = f"/{marker}"
                    expected_type = {
                        ".docx": DOCX_MAIN_CONTENT_TYPE,
                        ".xlsx": XLSX_MAIN_CONTENT_TYPE,
                    }[extension]
                    matching_main_part = False
                    with archive.open(content_info) as content_types:
                        for _event, element in ET.iterparse(content_types, events=("end",)):
                            macro_content_type = macro_content_type or any(
                                "macroenabled" in value.casefold()
                                or "vbaproject" in value.casefold()
                                for value in element.attrib.values()
                            )
                            matching_main_part = matching_main_part or (
                                element.attrib.get("PartName", "").casefold()
                                == expected_part.casefold()
                                and element.attrib.get("ContentType", "").casefold()
                                == expected_type.casefold()
                            )
                            element.clear()
                except (DefusedXmlException, ET.ParseError) as exc:
                    raise JobFailure(
                        ErrorCode.INVALID_FILE, "The document structure is invalid"
                    ) from exc
                if macro_content_type:
                    raise JobFailure(
                        ErrorCode.OFFICE_MACROS_NOT_ALLOWED,
                        "Office files containing macros are not supported",
                    )
                if not matching_main_part:
                    raise JobFailure(ErrorCode.INVALID_FILE, "The document structure is invalid")
                return

            if "mimetype" not in names or "content.xml" not in names:
                raise JobFailure(ErrorCode.INVALID_FILE, "The document structure is invalid")
            expected_mime = {
                ".odt": b"application/vnd.oasis.opendocument.text",
                ".ods": b"application/vnd.oasis.opendocument.spreadsheet",
            }[extension]
            if archive.read("mimetype").strip() != expected_mime:
                raise JobFailure(ErrorCode.INVALID_FILE, "The document structure is invalid")
            manifest_name = "meta-inf/manifest.xml"
            if manifest_name in names:
                try:
                    manifest_info = next(
                        info for info in infos if info.filename.casefold() == manifest_name
                    )
                    if manifest_info.file_size > MAX_OFFICE_METADATA_BYTES:
                        raise JobFailure(
                            ErrorCode.OFFICE_TOO_COMPLEX, "The Office file is too complex"
                        )
                    encrypted = False
                    with archive.open(manifest_info) as manifest:
                        for _event, element in ET.iterparse(manifest, events=("end",)):
                            encrypted = encrypted or (
                                element.tag.rsplit("}", 1)[-1] == "encryption-data"
                            )
                            element.clear()
                except (DefusedXmlException, ET.ParseError) as exc:
                    raise JobFailure(
                        ErrorCode.INVALID_FILE, "The document structure is invalid"
                    ) from exc
                if encrypted:
                    raise JobFailure(
                        ErrorCode.OFFICE_PASSWORD_PROTECTED,
                        "Password-protected Office files are not supported",
                    )
            if _odf_has_active_content(archive, infos):
                raise JobFailure(
                    ErrorCode.OFFICE_MACROS_NOT_ALLOWED,
                    "Office files containing macros are not supported",
                )
    except JobFailure:
        raise
    except (KeyError, OSError, zipfile.BadZipFile) as exc:
        raise JobFailure(ErrorCode.INVALID_FILE, "The document container is invalid") from exc


def validate_upscale_dimensions(path: Path, scale: int | str) -> None:
    try:
        scale = int(scale)
    except (TypeError, ValueError) as exc:
        raise JobFailure(ErrorCode.INVALID_FILE, "Upscale factor must be 2 or 4") from exc
    if scale not in {2, 4}:
        raise JobFailure(ErrorCode.INVALID_FILE, "Upscale factor must be 2 or 4")
    with Image.open(path) as image:
        width, height = image.size
    output_pixels = width * height * scale * scale
    if output_pixels > MAX_UPSCALE_OUTPUT_PIXELS:
        raise JobFailure(
            ErrorCode.IMAGE_TOO_LARGE,
            "The image is too large for the selected upscale factor",
            {
                "width": width,
                "height": height,
                "scale": scale,
                "maxOutputPixels": MAX_UPSCALE_OUTPUT_PIXELS,
            },
        )


def validate_content_type(extension: str, content_type: str | None) -> None:
    if extension not in OFFICE_MIME_TYPES:
        return
    normalized = (content_type or "").split(";", 1)[0].strip().casefold()
    if normalized in GENERIC_UPLOAD_TYPES or normalized in OFFICE_MIME_TYPES[extension]:
        return
    raise JobFailure(
        ErrorCode.INVALID_FILE,
        "The declared file type does not match the file extension",
        {"extension": extension, "contentType": normalized},
    )


def validate_signature(path: Path, extension: str, content_type: str | None = None) -> None:
    validate_content_type(extension, content_type)
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

    if extension in {".docx", ".xlsx", ".odt", ".ods"}:
        _validate_zip_office(path, extension)
        return

    if extension in {".doc", ".xls"}:
        if not head.startswith(OLE_MAGIC):
            label = "Word document" if extension == ".doc" else "Excel workbook"
            raise JobFailure(ErrorCode.INVALID_FILE, f"The legacy {label} is invalid")
        _validate_legacy_office(path, extension)
        return
    if extension == ".rtf" and not head.lstrip().startswith(b"{\\rtf"):
        raise JobFailure(ErrorCode.INVALID_FILE, "The RTF document is invalid")
    if extension == ".csv":
        return


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


def _scanner_ready_sync() -> bool:
    if not settings.clamav_required:
        return True
    try:
        scanner = clamd.ClamdNetworkSocket(settings.clamav_host, settings.clamav_port, timeout=5)
        return bool(scanner.ping())
    except Exception:
        return False


async def scan_file(path: Path) -> None:
    await asyncio.to_thread(_scan_sync, path)


async def scanner_ready() -> bool:
    return await asyncio.to_thread(_scanner_ready_sync)
