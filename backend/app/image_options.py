from __future__ import annotations

import re

from .models import ErrorCode, JobFailure, Operation

MODELS = {"standard", "photo", "web-photo", "detail", "illustration"}


def validate_image_options(operation: Operation, options: dict) -> None:
    def invalid(message):
        raise JobFailure(ErrorCode.INVALID_FILE, message)

    for key in ("model", "format", "backgroundPreset", "background", "language"):
        if key in options and not isinstance(options[key], str):
            invalid(f"{key} must be a string")
    if operation in {
        Operation.UPSCALE,
        Operation.UPSCALE_PREVIEW,
        Operation.IMAGE_PIPELINE,
        Operation.IMAGE_ENHANCE,
        Operation.REMOVE_BACKGROUND,
    }:
        try:
            scale = int(
                options.get(
                    "scale",
                    1 if operation in {Operation.IMAGE_ENHANCE, Operation.IMAGE_PIPELINE} else 2,
                )
            )
            strength = int(options.get("strength", 100))
            quality = int(options.get("quality", 100))
            width = int(options.get("maxWidth", 0))
            height = int(options.get("maxHeight", 0))
        except (ValueError, TypeError, OverflowError):
            invalid("Invalid numeric image options")
        if scale not in (1, 2, 4) or not 0 <= strength <= 100 or not 1 <= quality <= 100:
            invalid("Invalid scale, strength or quality")
        if not 0 <= width <= 16000 or not 0 <= height <= 16000:
            invalid("Output dimensions must be between 0 and 16000")
        if operation in {Operation.UPSCALE, Operation.UPSCALE_PREVIEW} and scale == 1:
            invalid("Upscale factor must be 2 or 4")
        model = options.get("model", "standard")
        if model not in MODELS or (model == "photo" and scale > 1 and scale != 2):
            invalid("This model supports only 2x")
        if options.get("format", "png") not in {"png", "jpeg", "jpg", "webp"}:
            invalid("Unsupported output format")
        if operation is Operation.REMOVE_BACKGROUND and options.get("format") in {"jpeg", "jpg"}:
            invalid("Background removal needs PNG or WebP")
        if options.get("backgroundPreset", "quality") not in {"fast", "quality", "portrait"}:
            invalid("Unsupported background preset")
        if not re.fullmatch(
            r"transparent|#[0-9a-fA-F]{6}", str(options.get("background", "transparent"))
        ):
            invalid("Invalid background color")
        for flag in ("removeBackground", "enhance"):
            if flag in options and not isinstance(options[flag], bool):
                invalid(f"{flag} must be boolean")
    if operation is Operation.OCR:
        if options.get("format", "docx") not in {"docx", "txt", "pdf"}:
            invalid("OCR output must be DOCX, TXT or PDF")
        if options.get("language", "rus+eng") not in {"rus+eng", "rus", "eng"}:
            invalid("Unsupported OCR language")
