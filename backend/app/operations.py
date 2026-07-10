from __future__ import annotations

import asyncio
import base64
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pikepdf
import pypdfium2 as pdfium
from pdf2docx import Converter
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

from .config import settings
from .models import ErrorCode, JobFailure, Operation

register_heif_opener()

MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".zip": "application/zip",
}


def clean_stem(name: str) -> str:
    stem = Path(name).stem
    cleaned = re.sub(r"[^\w.-]+", "_", stem, flags=re.UNICODE).strip("._")
    return cleaned[:80] or "result"


def image_extension(fmt: str) -> str:
    return {"jpeg": ".jpg", "jpg": ".jpg", "png": ".png", "webp": ".webp"}.get(fmt, ".png")


def image_save_format(fmt: str) -> str:
    return {"jpeg": "JPEG", "jpg": "JPEG", "png": "PNG", "webp": "WEBP"}.get(fmt, "PNG")


def save_image(image: Image.Image, destination: Path, fmt: str, quality: int = 90) -> None:
    output_format = image_save_format(fmt)
    if output_format == "JPEG":
        if image.mode in {"RGBA", "LA"}:
            background = Image.new("RGB", image.size, "white")
            background.paste(image, mask=image.getchannel("A"))
            image = background
        else:
            image = image.convert("RGB")
        image.save(destination, output_format, quality=quality, optimize=True, progressive=True)
    elif output_format == "WEBP":
        image.save(destination, output_format, quality=quality, method=5)
    else:
        image.save(destination, output_format, optimize=True, compress_level=9)


def package_outputs(
    output_dir: Path, outputs: list[Path], archive_name: str = "results.zip"
) -> Path:
    if len(outputs) == 1:
        return outputs[0]
    archive = output_dir / archive_name
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zipped:
        for path in outputs:
            zipped.write(path, arcname=path.name)
    for path in outputs:
        path.unlink(missing_ok=True)
    return archive


def ensure_result_limit(path: Path) -> None:
    if path.stat().st_size > settings.max_result_bytes:
        path.unlink(missing_ok=True)
        raise JobFailure(ErrorCode.RESULT_TOO_LARGE, "The generated result is too large")


def convert_images(files: list[dict[str, Any]], output_dir: Path, options: dict[str, Any]) -> Path:
    fmt = str(options.get("format", "webp")).lower()
    if fmt not in {"jpg", "jpeg", "png", "webp"}:
        raise JobFailure(ErrorCode.UNSUPPORTED_FORMAT, "Unsupported output image format")
    quality = max(1, min(100, int(options.get("quality", 90))))
    max_width = max(0, int(options.get("maxWidth", 0) or 0))
    max_height = max(0, int(options.get("maxHeight", 0) or 0))
    outputs: list[Path] = []
    for index, item in enumerate(files, 1):
        with Image.open(item["path"]) as source:
            image = ImageOps.exif_transpose(source)
            image.load()
            if max_width or max_height:
                width_limit = max_width or image.width
                height_limit = max_height or image.height
                if image.width > width_limit or image.height > height_limit:
                    image.thumbnail((width_limit, height_limit), Image.Resampling.LANCZOS)
            destination = (
                output_dir / f"{clean_stem(item['original'])}_{index}{image_extension(fmt)}"
            )
            save_image(image, destination, fmt, quality)
            outputs.append(destination)
    return package_outputs(output_dir, outputs, "converted-images.zip")


async def upscale(files: list[dict[str, Any]], output_dir: Path, options: dict[str, Any]) -> Path:
    scale = int(options.get("scale", 2))
    if scale not in {2, 4}:
        raise JobFailure(ErrorCode.INVALID_FILE, "Upscale factor must be 2 or 4")
    fmt = str(options.get("format", "jpeg")).lower()
    if fmt not in {"jpg", "jpeg", "png", "webp"}:
        raise JobFailure(ErrorCode.UNSUPPORTED_FORMAT, "Unsupported output image format")
    upstream_format = "png" if fmt == "webp" else ("jpeg" if fmt == "jpg" else fmt)
    outputs: list[Path] = []
    timeout = httpx.Timeout(settings.job_timeout_seconds, connect=30)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for index, item in enumerate(files, 1):
            with Path(item["path"]).open("rb") as source:
                response = await client.post(
                    settings.upscale_url,
                    files={
                        "file": (
                            item["original"],
                            source,
                            item.get("content_type") or "application/octet-stream",
                        )
                    },
                    data={"scale": str(scale), "format": upstream_format},
                )
            if response.status_code >= 400:
                raise JobFailure(
                    ErrorCode.UPSTREAM_ERROR, "The upscaling service returned an error"
                )
            upstream_extension = upstream_format.replace("jpeg", "jpg")
            raw = output_dir / (
                f"upscaled_{clean_stem(item['original'])}_{index}.{upstream_extension}"
            )
            raw.write_bytes(response.content)
            if fmt == "webp":
                destination = output_dir / f"upscaled_{clean_stem(item['original'])}_{index}.webp"
                with Image.open(raw) as image:
                    save_image(image, destination, "webp", int(options.get("quality", 90)))
                raw.unlink(missing_ok=True)
            else:
                destination = raw
            outputs.append(destination)
    return package_outputs(output_dir, outputs, "upscaled-images.zip")


async def remove_background(
    files: list[dict[str, Any]], output_dir: Path, options: dict[str, Any]
) -> Path:
    fmt = str(options.get("format", "png")).lower()
    if fmt not in {"png", "webp"}:
        raise JobFailure(ErrorCode.UNSUPPORTED_FORMAT, "Background removal supports PNG and WebP")
    outputs: list[Path] = []
    timeout = httpx.Timeout(settings.job_timeout_seconds, connect=30)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for index, item in enumerate(files, 1):
            with Path(item["path"]).open("rb") as source:
                response = await client.post(
                    settings.background_url,
                    files={
                        "file": (
                            item["original"],
                            source,
                            item.get("content_type") or "application/octet-stream",
                        )
                    },
                )
            if response.status_code >= 400:
                raise JobFailure(
                    ErrorCode.UPSTREAM_ERROR, "The background removal service returned an error"
                )
            try:
                payload = response.json()
                image_bytes = base64.b64decode(payload["result_image"], validate=True)
            except Exception as exc:
                raise JobFailure(
                    ErrorCode.UPSTREAM_ERROR, "The background removal response was invalid"
                ) from exc
            png = output_dir / f"no-background_{clean_stem(item['original'])}_{index}.png"
            png.write_bytes(image_bytes)
            if fmt == "webp":
                destination = png.with_suffix(".webp")
                with Image.open(png) as image:
                    save_image(image, destination, "webp", int(options.get("quality", 90)))
                png.unlink(missing_ok=True)
            else:
                destination = png
            outputs.append(destination)
    return package_outputs(output_dir, outputs, "background-removed.zip")


def pdf_has_text_layer(path: Path) -> bool:
    try:
        document = pdfium.PdfDocument(str(path))
        pages_with_text = 0
        total_chars = 0
        for page in document:
            text_page = page.get_textpage()
            text = text_page.get_text_bounded().strip()
            count = len(text)
            total_chars += count
            if count >= 20:
                pages_with_text += 1
        required_pages = max(1, (len(document) + 1) // 2)
        return total_chars >= 50 and pages_with_text >= required_pages
    except Exception as exc:
        raise JobFailure(ErrorCode.INVALID_FILE, "The PDF could not be read") from exc


def convert_documents(
    files: list[dict[str, Any]], output_dir: Path, options: dict[str, Any]
) -> Path:
    outputs: list[Path] = []
    for index, item in enumerate(files, 1):
        source = Path(item["path"])
        suffix = source.suffix.lower()
        stem = clean_stem(item["original"])
        if suffix == ".pdf":
            if not pdf_has_text_layer(source):
                raise JobFailure(
                    ErrorCode.PDF_NO_TEXT_LAYER,
                    "No text layer was found. Converting scanned PDFs to Word is not supported.",
                )
            destination = output_dir / f"{stem}_{index}.docx"
            converter = Converter(str(source))
            try:
                converter.convert(str(destination), start=0, end=None)
            finally:
                converter.close()
            outputs.append(destination)
            continue

        profile = output_dir / f"lo-profile-{index}"
        profile.mkdir()
        command = [
            "libreoffice",
            "--headless",
            f"-env:UserInstallation=file://{profile.as_posix()}",
            "--convert-to",
            "pdf:writer_pdf_Export",
            "--outdir",
            str(output_dir),
            str(source),
        ]
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=600, check=False
            )
        except subprocess.TimeoutExpired as exc:
            raise JobFailure(ErrorCode.TIMEOUT, "Document conversion timed out") from exc
        finally:
            shutil.rmtree(profile, ignore_errors=True)
        generated = output_dir / f"{source.stem}.pdf"
        if completed.returncode != 0 or not generated.exists():
            raise JobFailure(ErrorCode.INVALID_FILE, "LibreOffice could not convert the document")
        destination = output_dir / f"{stem}_{index}.pdf"
        generated.replace(destination)
        outputs.append(destination)
    return package_outputs(output_dir, outputs, "converted-documents.zip")


def merge_pdfs(files: list[dict[str, Any]], output_dir: Path) -> Path:
    if len(files) < 2:
        raise JobFailure(ErrorCode.INVALID_FILE, "At least two PDF files are required")
    destination = output_dir / "merged.pdf"
    merged = pikepdf.Pdf.new()
    for item in files:
        with pikepdf.open(item["path"]) as document:
            merged.pages.extend(document.pages)
    merged.save(destination)
    merged.close()
    return destination


def parse_ranges(value: str, page_count: int) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for raw_part in value.split(","):
        part = raw_part.strip()
        if not part:
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not match:
            raise JobFailure(ErrorCode.INVALID_FILE, "Invalid page range")
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start < 1 or end < start or end > page_count:
            raise JobFailure(ErrorCode.INVALID_FILE, "Page range is outside the document")
        ranges.append((start - 1, end - 1))
    if not ranges:
        raise JobFailure(ErrorCode.INVALID_FILE, "At least one page range is required")
    return ranges


def split_pdf(files: list[dict[str, Any]], output_dir: Path, options: dict[str, Any]) -> Path:
    if len(files) != 1:
        raise JobFailure(ErrorCode.INVALID_FILE, "PDF splitting accepts one file")
    outputs: list[Path] = []
    with pikepdf.open(files[0]["path"]) as source:
        if options.get("mode") == "each":
            ranges = [(index, index) for index in range(len(source.pages))]
        else:
            ranges = parse_ranges(str(options.get("ranges", "")), len(source.pages))
        for number, (start, end) in enumerate(ranges, 1):
            result = pikepdf.Pdf.new()
            result.pages.extend(source.pages[start : end + 1])
            destination = output_dir / f"pages-{start + 1}-{end + 1}_{number}.pdf"
            result.save(destination)
            result.close()
            outputs.append(destination)
    return package_outputs(output_dir, outputs, "split-pdf.zip")


def images_to_pdf(files: list[dict[str, Any]], output_dir: Path, options: dict[str, Any]) -> Path:
    page_mode = str(options.get("pageSize", "a4"))
    margin_mm = max(0, min(30, int(options.get("margin", 10))))
    orientation = str(options.get("orientation", "auto"))
    dpi = 150
    pages: list[Image.Image] = []
    for item in files:
        with Image.open(item["path"]) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.load()
        if page_mode == "original":
            pages.append(image)
            continue
        landscape = orientation == "landscape" or (
            orientation == "auto" and image.width > image.height
        )
        page_size = (1754, 1240) if landscape else (1240, 1754)
        margin_px = round(margin_mm / 25.4 * dpi)
        canvas = Image.new("RGB", page_size, "white")
        fitted = ImageOps.contain(
            image,
            (page_size[0] - 2 * margin_px, page_size[1] - 2 * margin_px),
            Image.Resampling.LANCZOS,
        )
        canvas.paste(
            fitted, ((page_size[0] - fitted.width) // 2, (page_size[1] - fitted.height) // 2)
        )
        pages.append(canvas)
    if not pages:
        raise JobFailure(ErrorCode.INVALID_FILE, "At least one image is required")
    destination = output_dir / "images.pdf"
    pages[0].save(destination, "PDF", save_all=True, append_images=pages[1:], resolution=dpi)
    return destination


def pdf_to_images(files: list[dict[str, Any]], output_dir: Path, options: dict[str, Any]) -> Path:
    if len(files) != 1:
        raise JobFailure(ErrorCode.INVALID_FILE, "PDF rendering accepts one file")
    fmt = str(options.get("format", "png")).lower()
    if fmt not in {"jpg", "jpeg", "png", "webp"}:
        raise JobFailure(ErrorCode.UNSUPPORTED_FORMAT, "Unsupported output image format")
    dpi = int(options.get("dpi", 150))
    if dpi not in {150, 300}:
        raise JobFailure(ErrorCode.INVALID_FILE, "DPI must be 150 or 300")
    document = pdfium.PdfDocument(files[0]["path"])
    outputs: list[Path] = []
    for number, page in enumerate(document, 1):
        bitmap = page.render(scale=dpi / 72)
        image = bitmap.to_pil()
        destination = output_dir / f"page-{number:03d}{image_extension(fmt)}"
        save_image(image, destination, fmt, int(options.get("quality", 90)))
        outputs.append(destination)
    return package_outputs(output_dir, outputs, "pdf-pages.zip")


async def execute(
    operation: Operation, files: list[dict[str, Any]], output_dir: Path, options: dict[str, Any]
) -> Path:
    if operation is Operation.UPSCALE:
        result = await upscale(files, output_dir, options)
    elif operation is Operation.REMOVE_BACKGROUND:
        result = await remove_background(files, output_dir, options)
    elif operation is Operation.IMAGE_CONVERT:
        result = await asyncio.to_thread(convert_images, files, output_dir, options)
    elif operation is Operation.DOCUMENT_CONVERT:
        result = await asyncio.to_thread(convert_documents, files, output_dir, options)
    elif operation is Operation.PDF_MERGE:
        result = await asyncio.to_thread(merge_pdfs, files, output_dir)
    elif operation is Operation.PDF_SPLIT:
        result = await asyncio.to_thread(split_pdf, files, output_dir, options)
    elif operation is Operation.IMAGES_TO_PDF:
        result = await asyncio.to_thread(images_to_pdf, files, output_dir, options)
    elif operation is Operation.PDF_TO_IMAGES:
        result = await asyncio.to_thread(pdf_to_images, files, output_dir, options)
    else:
        raise JobFailure(ErrorCode.UNSUPPORTED_FORMAT, "Unknown operation")
    ensure_result_limit(result)
    return result


def result_mime(path: Path) -> str:
    return MIME_BY_SUFFIX.get(path.suffix.lower(), "application/octet-stream")
