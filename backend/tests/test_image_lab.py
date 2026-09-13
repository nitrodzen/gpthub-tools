from io import BytesIO
from pathlib import Path

import fitz
import httpx
import pytest
from docx import Document
from PIL import Image

from app.image_options import validate_image_options
from app.image_pipeline import image_pipeline
from app.models import AI_OPERATIONS, JobFailure, Operation
from app.ocr import recognize
from app.operations import upscale
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
        {"faceRestoration": 101},
        {"faceRestoration": "strong"},
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
@pytest.mark.parametrize("scale,enhance,expected", [(2, True, [0, 100]), (1, False, [100])])
async def test_pipeline_restores_faces_once_at_final_ai_stage(
    tmp_path, monkeypatch, scale, enhance, expected
):
    calls = []
    source = tmp_path / "portrait.png"
    Image.new("RGB", (30, 40)).save(source)

    async def fake_upscale(files, folder, options, warnings=None):
        calls.append(options["faceRestoration"])
        result = folder / "restored.png"
        with Image.open(files[0]["path"]) as image:
            image.resize((image.width * options["scale"], image.height * options["scale"])).save(
                result
            )
        return result

    monkeypatch.setattr("app.image_pipeline.upscale", fake_upscale)
    result = await image_pipeline(
        [{"path": str(source), "original": source.name}],
        tmp_path,
        {"removeBackground": False, "enhance": enhance, "scale": scale, "faceRestoration": 100},
    )
    assert calls == expected
    assert Image.open(result).size == (30 * scale, 40 * scale)


@pytest.mark.asyncio
@pytest.mark.parametrize("restoration,expected_host", [(0, "cpu.test"), (100, "gpu.test")])
async def test_native_face_reconstruction_routes_to_gpu(
    tmp_path, monkeypatch, restoration, expected_host
):
    image = tmp_path / "source.png"
    Image.new("RGB", (30, 40)).save(image)
    output = BytesIO()
    Image.new("RGB", (30, 40), "blue").save(output, "PNG")
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            content=output.getvalue(),
            headers={"content-type": "image/png", "x-faces-restored": "0"},
        )

    client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.operations.httpx.AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs),
    )
    from dataclasses import replace

    from app.operations import settings

    monkeypatch.setattr(
        "app.operations.settings",
        replace(
            settings, enhance_url="http://cpu.test/enhance", upscale_url="http://gpu.test/upscale"
        ),
    )
    warnings = []
    await upscale(
        [{"path": str(image), "original": image.name}],
        tmp_path,
        {"scale": 1, "model": "clean", "format": "png", "faceRestoration": restoration},
        warnings=warnings,
    )
    assert requests[0].url.host == expected_host
    assert [warning.code for warning in warnings] == (["FACE_NOT_FOUND"] if restoration else [])
    assert b'name="face_restoration"' in requests[0].content
    assert str(restoration).encode() in requests[0].content


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
