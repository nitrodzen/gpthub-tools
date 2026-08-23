from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from .models import ErrorCode, JobFailure, Operation
from .office_operations import excel_to_word, word_to_excel


def _write_payload(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def run(control_path: Path, result_path: Path) -> int:
    try:
        control = json.loads(control_path.read_text(encoding="utf-8"))
        operation = Operation(control["operation"])
        if operation not in {Operation.WORD_TO_EXCEL, Operation.EXCEL_TO_WORD}:
            raise JobFailure(ErrorCode.UNSUPPORTED_FORMAT, "Unsupported Office operation")
        output_dir = Path(control["outputDir"])
        files = control["files"]
        options = dict(control.get("options") or {})
        timeout_seconds = float(control["timeoutSeconds"])
        options["_deadlineMonotonic"] = time.monotonic() + timeout_seconds
        if operation is Operation.WORD_TO_EXCEL:
            result = word_to_excel(files, output_dir, options)
        else:
            result = excel_to_word(files, output_dir, options)
        _write_payload(
            result_path,
            {
                "ok": True,
                "path": str(result.path),
                "warnings": [warning.model_dump(exclude_none=True) for warning in result.warnings],
            },
        )
        return 0
    except JobFailure as exc:
        _write_payload(
            result_path,
            {
                "ok": False,
                "error": {
                    "code": exc.code.value,
                    "message": exc.message,
                    "details": exc.details,
                },
            },
        )
        return 2
    except Exception:
        _write_payload(
            result_path,
            {
                "ok": False,
                "error": {
                    "code": ErrorCode.OFFICE_CONVERSION_FAILED.value,
                    "message": "The Office file could not be converted",
                },
            },
        )
        return 3


def main() -> int:
    if len(sys.argv) != 3:
        return 64
    return run(Path(sys.argv[1]), Path(sys.argv[2]))


if __name__ == "__main__":
    raise SystemExit(main())
