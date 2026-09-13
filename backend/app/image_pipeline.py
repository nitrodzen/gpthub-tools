from __future__ import annotations

import tempfile
from pathlib import Path

from PIL import Image, ImageOps

from .models import JobWarning
from .operations import image_extension, package_outputs, remove_background, save_image, upscale


async def image_pipeline(
    files: list[dict], output_dir: Path, options: dict, warnings: list[JobWarning] | None = None
) -> Path:
    outputs = []
    for index, item in enumerate(files, 1):
        with tempfile.TemporaryDirectory(prefix=".pipeline-", dir=output_dir) as temporary:
            root = Path(temporary)
            source = Path(item["path"])
            current = {**item}
            stages = []
            if options.get("removeBackground", True):
                stages.append(("remove", remove_background, {**options, "format": "png"}))
            scale = int(options.get("scale", 1))
            if options.get("enhance", False) or (scale == 1 and options.get("faceRestoration", 0)):
                stages.append(
                    (
                        "enhance",
                        upscale,
                        {
                            **options,
                            "format": "png",
                            "model": "clean",
                            "scale": 1,
                            "faceRestoration": options.get("faceRestoration", 0)
                            if scale == 1
                            else 0,
                        },
                    )
                )
            if scale > 1:
                stages.append(("upscale", upscale, {**options, "format": "png"}))
            for name, handler, parameters in stages:
                folder = root / name
                folder.mkdir()
                if handler is upscale:
                    source = await handler([current], folder, parameters, warnings=warnings)
                else:
                    source = await handler([current], folder, parameters)
                current = {
                    "path": str(source),
                    "original": "image.png",
                    "content_type": "image/png",
                }
            with Image.open(source) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGBA")
                image.load()
            width, height = int(options.get("maxWidth", 0)), int(options.get("maxHeight", 0))
            if width or height:
                image.thumbnail(
                    (width or image.width, height or image.height), Image.Resampling.LANCZOS
                )
            color = options.get("background", "transparent")
            if color != "transparent":
                canvas = Image.new("RGBA", image.size, color)
                canvas.alpha_composite(image)
                image = canvas.convert("RGB")
            fmt = options.get("format", "png")
            destination = output_dir / f"prepared-{index}{image_extension(fmt)}"
            save_image(image, destination, fmt, int(options.get("quality", 100)))
            outputs.append(destination)
    return package_outputs(output_dir, outputs, "prepared-images.zip")
