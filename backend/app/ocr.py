"""OCR in a killable child process; no network access in local workers."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .config import settings
from .models import ErrorCode, JobFailure, JobWarning
from .office_operations import OperationResult


async def run_ocr_isolated(files: list[dict], output_dir: Path, options: dict) -> OperationResult:
    from .operations import ensure_result_limit, terminate_office_process

    with tempfile.TemporaryDirectory(prefix=".ocr-request-", dir=output_dir) as temporary:
        request = Path(temporary) / "request.json"
        response = Path(temporary) / "response.json"
        request.write_text(
            json.dumps({"files": files, "output": str(output_dir), "options": options}),
            encoding="utf-8",
        )
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "app.ocr",
            str(request),
            str(response),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=os.name == "posix",
        )
        try:
            await asyncio.wait_for(
                process.wait(), timeout=min(900, settings.job_timeout_seconds - 30)
            )
        except (TimeoutError, asyncio.CancelledError) as exc:
            await terminate_office_process(process)
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise JobFailure(ErrorCode.TIMEOUT, "OCR timed out; try fewer pages") from exc
        if process.returncode or not response.is_file():
            raise JobFailure(ErrorCode.INVALID_FILE, "OCR could not process this file")
        payload = json.loads(response.read_text(encoding="utf-8"))
        if payload.get("error"):
            raise JobFailure(ErrorCode(payload["code"]), payload["error"])
        result = Path(payload["path"])
        ensure_result_limit(result)
        return OperationResult(
            result,
            [
                JobWarning(
                    code="OCR_REVIEW",
                    message=(
                        "Review recognized text for mistakes. "
                        "Word output uses a simple editable layout."
                    ),
                )
            ],
        )


def recognize(files: list[dict], output_dir: Path, options: dict) -> Path:
    import fitz
    from docx import Document
    from PIL import Image, ImageOps

    from .image_options import validate_image_options
    from .models import Operation
    from .operations import clean_stem, package_outputs

    validate_image_options(Operation.OCR, options)
    fmt = options.get("format", "docx")
    language = options.get("language", "rus+eng")
    outputs = []
    pages_seen = 0
    for number, item in enumerate(files, 1):
        source = Path(item["path"])
        pdf = fitz.open(source) if source.suffix.lower() == ".pdf" else None
        count = len(pdf) if pdf else 1
        pages_seen += count
        if pages_seen > 50:
            raise JobFailure(ErrorCode.PDF_TOO_MANY_PAGES, "OCR accepts at most 50 pages per job")
        destination = output_dir / f"{clean_stem(item['original'])}-ocr-{number}.{fmt}"
        texts = []
        combined = fitz.open() if fmt == "pdf" else None
        try:
            with tempfile.TemporaryDirectory(prefix=".ocr-pages-", dir=output_dir) as temporary:
                root = Path(temporary)
                for page_number in range(count):
                    page = pdf[page_number] if pdf else None
                    existing = page.get_text().strip() if page is not None else ""
                    if len(existing) >= 20:
                        texts.append(existing)
                        if combined is not None:
                            combined.insert_pdf(pdf, from_page=page_number, to_page=page_number)
                        continue
                    if page is not None:
                        factor = min(
                            200 / 72,
                            (20_000_000 / max(1, page.rect.width * page.rect.height)) ** 0.5,
                        )
                        pixmap = page.get_pixmap(matrix=fitz.Matrix(factor, factor), alpha=False)
                        image = Image.frombytes(
                            "RGB", (pixmap.width, pixmap.height), pixmap.samples
                        )
                    else:
                        with Image.open(source) as opened:
                            image = ImageOps.exif_transpose(opened).convert("RGB")
                            image.load()
                        image.thumbnail((4500, 4500), Image.Resampling.LANCZOS)
                    raster = root / "page.png"
                    image.save(raster, "PNG", dpi=(200, 200))
                    target = root / "recognized"
                    output_type = "pdf" if fmt == "pdf" else "txt"
                    try:
                        completed = subprocess.run(
                            [
                                "tesseract",
                                str(raster),
                                str(target),
                                "-l",
                                language,
                                "--psm",
                                "3",
                                "--dpi",
                                "200",
                                output_type,
                            ],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE,
                            timeout=90,
                            env={**os.environ, "OMP_THREAD_LIMIT": "2"},
                            check=False,
                        )
                    except subprocess.TimeoutExpired as exc:
                        raise JobFailure(ErrorCode.TIMEOUT, "OCR page timed out") from exc
                    recognized = target.with_suffix("." + output_type)
                    if completed.returncode or not recognized.exists():
                        raise JobFailure(ErrorCode.INVALID_FILE, "Text recognition failed")
                    if combined is not None:
                        with fitz.open(recognized) as part:
                            combined.insert_pdf(part)
                    else:
                        texts.append(recognized.read_text(encoding="utf-8").strip())
                    recognized.unlink()
            if combined is not None:
                combined.save(destination, garbage=4, deflate=True)
            elif fmt == "txt":
                destination.write_text("\n\n\f\n\n".join(texts), encoding="utf-8")
            else:
                document = Document()
                for index, text in enumerate(texts):
                    if index:
                        document.add_page_break()
                    for line in text.splitlines():
                        document.add_paragraph(
                            "".join(c for c in line if c in "\t" or ord(c) >= 32)
                        )
                document.save(destination)
            outputs.append(destination)
        finally:
            if pdf is not None:
                pdf.close()
            if combined is not None:
                combined.close()
    return package_outputs(output_dir, outputs, "recognized-documents.zip")


if __name__ == "__main__":
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    try:
        result = recognize(data["files"], Path(data["output"]), data["options"])
        payload = {"path": str(result)}
    except JobFailure as failure:
        payload = {"error": failure.message, "code": failure.code.value}
    Path(sys.argv[2]).write_text(json.dumps(payload), encoding="utf-8")
