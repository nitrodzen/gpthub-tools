from pathlib import Path

import fitz
import pytest
from docx import Document
from PIL import Image

from app.image_options import validate_image_options
from app.image_pipeline import image_pipeline
from app.models import AI_OPERATIONS, JobFailure, Operation
from app.ocr import recognize
from app.security import allowed_extensions


def test_additive_operations_keep_queue_and_file_limits():
    assert Operation.UPSCALE_PREVIEW in AI_OPERATIONS
    assert Operation.IMAGE_PIPELINE in AI_OPERATIONS
    assert Operation.OCR not in AI_OPERATIONS
    assert ".pdf" in allowed_extensions(Operation.OCR)
    assert ".png" in allowed_extensions(Operation.OCR)
    assert ".docx" not in allowed_extensions(Operation.OCR)


@pytest.mark.parametrize(
    "options",
    [
        {"model": "../weights"},
        {"model": []},
        {"format": {}},
        {"scale": 3},
        {"scale": 4, "model": "photo"},
        {"strength": 101},
        {"background": "url(evil)"},
        {"maxWidth": 100000},
        {"removeBackground": "false"},
        {"quality": None},
    ],
)
def test_invalid_image_options_fail_before_processing(options):
    with pytest.raises(JobFailure):
        validate_image_options(Operation.IMAGE_PIPELINE, options)


@pytest.mark.asyncio
async def test_pipeline_preserves_alpha_and_can_composite_without_ai(tmp_path: Path):
    source = tmp_path / "transparent.png"
    Image.new("RGBA", (80, 40), (255, 0, 0, 128)).save(source)
    item = {"path": str(source), "original": source.name}
    target = tmp_path / "out"
    target.mkdir()
    result = await image_pipeline(
        [item],
        target,
        {
            "removeBackground": False,
            "scale": 1,
            "format": "png",
            "maxWidth": 40,
        },
    )
    with Image.open(result) as image:
        assert image.size == (40, 20)
        assert image.getpixel((0, 0))[3] == 128
    result = await image_pipeline(
        [item],
        target,
        {
            "removeBackground": False,
            "scale": 1,
            "format": "png",
            "background": "#ffffff",
        },
    )
    with Image.open(result) as image:
        assert image.getpixel((0, 0)) == (255, 127, 127)
    assert not list(target.glob(".pipeline-*"))


def test_ocr_preserves_existing_text_without_requiring_tesseract(tmp_path: Path):
    source = tmp_path / "existing.pdf"
    with fitz.open() as pdf:
        pdf.new_page().insert_text((50, 80), "Existing text should remain searchable and editable.")
        pdf.save(source)
    item = {"path": str(source), "original": source.name}
    result = recognize([item], tmp_path, {"format": "docx"})
    assert "Existing text" in "\n".join(p.text for p in Document(result).paragraphs)
    result = recognize([item], tmp_path, {"format": "pdf"})
    with fitz.open(result) as pdf:
        assert "Existing text" in pdf[0].get_text()


def test_ocr_rejects_more_than_fifty_pages(tmp_path: Path):
    source = tmp_path / "many.pdf"
    with fitz.open() as pdf:
        for _ in range(51):
            pdf.new_page()
        pdf.save(source)
    with pytest.raises(JobFailure, match="50 pages"):
        recognize([{"path": str(source), "original": source.name}], tmp_path, {})
