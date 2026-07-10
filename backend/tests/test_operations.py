from pathlib import Path

import pikepdf
import pytest
from PIL import Image

from app.models import ErrorCode, JobFailure
from app.operations import (
    convert_images,
    images_to_pdf,
    merge_pdfs,
    parse_ranges,
    pdf_has_text_layer,
    split_pdf,
)


def make_image(path: Path, color: str = "red") -> None:
    Image.new("RGB", (320, 180), color).save(path)


def make_pdf(path: Path, pages: int) -> None:
    document = pikepdf.Pdf.new()
    for _ in range(pages):
        document.add_blank_page(page_size=(595, 842))
    document.save(path)


def item(path: Path) -> dict:
    return {"path": str(path), "original": path.name, "content_type": "application/octet-stream"}


def test_parse_ranges() -> None:
    assert parse_ranges("1-3, 5, 8-10", 10) == [(0, 2), (4, 4), (7, 9)]
    with pytest.raises(JobFailure) as failure:
        parse_ranges("0-2", 10)
    assert failure.value.code == ErrorCode.INVALID_FILE


def test_image_conversion_and_images_to_pdf(tmp_path: Path) -> None:
    source = tmp_path / "photo.png"
    make_image(source)
    output = tmp_path / "output"
    output.mkdir()
    converted = convert_images(
        [item(source)], output, {"format": "webp", "quality": 80, "maxWidth": 120}
    )
    assert converted.suffix == ".webp"
    with Image.open(converted) as result:
        assert result.width == 120
    pdf = images_to_pdf(
        [item(source)], output, {"pageSize": "a4", "orientation": "auto", "margin": 10}
    )
    with pikepdf.open(pdf) as document:
        assert len(document.pages) == 1


def test_merge_and_split_pdf(tmp_path: Path) -> None:
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    make_pdf(first, 2)
    make_pdf(second, 1)
    output = tmp_path / "output"
    output.mkdir()
    merged = merge_pdfs([item(first), item(second)], output)
    with pikepdf.open(merged) as document:
        assert len(document.pages) == 3
    split = split_pdf([item(merged)], output, {"mode": "ranges", "ranges": "1-2,3"})
    assert split.suffix == ".zip"


def test_blank_pdf_has_no_text_layer(tmp_path: Path) -> None:
    source = tmp_path / "scan.pdf"
    make_pdf(source, 2)
    assert pdf_has_text_layer(source) is False
