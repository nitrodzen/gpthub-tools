from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Operation(StrEnum):
    UPSCALE = "upscale"
    REMOVE_BACKGROUND = "remove-background"
    IMAGE_CONVERT = "image-convert"
    DOCUMENT_CONVERT = "document-convert"
    PDF_MERGE = "pdf-merge"
    PDF_SPLIT = "pdf-split"
    IMAGES_TO_PDF = "images-to-pdf"
    PDF_TO_IMAGES = "pdf-to-images"


AI_OPERATIONS = {Operation.UPSCALE, Operation.REMOVE_BACKGROUND}


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ErrorCode(StrEnum):
    INVALID_FILE = "INVALID_FILE"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    JOB_TOO_LARGE = "JOB_TOO_LARGE"
    TOO_MANY_FILES = "TOO_MANY_FILES"
    RATE_LIMITED = "RATE_LIMITED"
    ACTIVE_JOB_EXISTS = "ACTIVE_JOB_EXISTS"
    MALWARE_DETECTED = "MALWARE_DETECTED"
    SCANNER_UNAVAILABLE = "SCANNER_UNAVAILABLE"
    PDF_NO_TEXT_LAYER = "PDF_NO_TEXT_LAYER"
    PDF_PASSWORD_PROTECTED = "PDF_PASSWORD_PROTECTED"
    PDF_TOO_MANY_PAGES = "PDF_TOO_MANY_PAGES"
    IMAGE_TOO_LARGE = "IMAGE_TOO_LARGE"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    TIMEOUT = "TIMEOUT"
    RESULT_TOO_LARGE = "RESULT_TOO_LARGE"
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    FORBIDDEN = "FORBIDDEN"
    CANCELLED = "CANCELLED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ApiError(BaseModel):
    code: ErrorCode
    message: str
    details: dict[str, Any] | None = None


class JobCreated(BaseModel):
    job_id: str = Field(alias="jobId")
    token: str
    status: JobStatus
    expires_at: str = Field(alias="expiresAt")

    model_config = {"populate_by_name": True}


class JobView(BaseModel):
    job_id: str = Field(alias="jobId")
    operation: Operation
    status: JobStatus
    progress: int = 0
    total: int = 0
    created_at: str = Field(alias="createdAt")
    expires_at: str = Field(alias="expiresAt")
    result_name: str | None = Field(default=None, alias="resultName")
    result_type: str | None = Field(default=None, alias="resultType")
    error: ApiError | None = None

    model_config = {"populate_by_name": True}


class JobFailure(Exception):
    def __init__(self, code: ErrorCode, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
