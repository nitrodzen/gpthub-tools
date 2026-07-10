# GPTHub Tools

Public, bilingual file tools for [tools.gpthub.ru](https://tools.gpthub.ru): image upscaling, background removal, image and document conversion, and practical PDF operations.

## Features

- Upscale PNG, JPEG, WebP, HEIC/HEIF, TIFF, and BMP images by 2x or 4x.
- Remove image backgrounds and export transparent PNG or WebP.
- Convert images to PNG, JPEG, or WebP with resizing, compression, EXIF orientation, and metadata removal.
- Convert DOC, DOCX, ODT, and RTF to PDF with LibreOffice.
- Convert text-based PDF files to DOCX. Scans without a text layer are rejected; OCR is intentionally out of scope.
- Merge and split PDFs, create PDFs from images, and render PDF pages to PNG, JPEG, or WebP.
- Process up to 20 files asynchronously with one-hour result retention.

## Architecture

- `frontend`: React, TypeScript, and Vite SPA served by unprivileged Nginx.
- `backend`: FastAPI upload/API service and ARQ workers.
- `redis`: transient queue and job metadata.
- `clamav`: upload malware scanning.
- `compose.yml`: production-shaped local stack with restricted containers and a loopback-only gateway.

The existing GPTHub image APIs remain the compute backends for upscaling and background removal. All conversions and PDF operations run locally in isolated workers.

## API

- `POST /api/jobs/{operation}` accepts multipart `files` and JSON `options`, then returns `202` with a job ID, capability token, and expiry time.
- `GET /api/jobs/{jobId}` returns queue state and progress.
- `GET /api/jobs/{jobId}/download` downloads the result; `DELETE /api/jobs/{jobId}` cancels and removes it.
- Send the capability token in the `X-Capability-Token` header for all job-specific `GET` and `DELETE` requests. Keeping it out of URLs prevents it from being recorded in normal access logs.

## Local development

Requirements: Docker Compose, or Node.js 22 and Python 3.12 for individual components.

```bash
docker compose up --build
```

Open `http://localhost:9080`. The first ClamAV start downloads signatures and can take several minutes.

Frontend checks:

```bash
cd frontend
npm ci
npm test
npm run build
```

Backend checks:

```bash
cd backend
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
ruff check app tests
PYTHONPATH=. python -m pytest
```

## Configuration

Copy `.env.example` to `.env` for overrides. Generate a unique `APP_SECRET` in production. The file is ignored by Git and must remain server-side.

Uploads are limited by file size, aggregate job size, type, signature, pixel/page count, malware scan, per-IP rate limits, and worker concurrency. Inputs are deleted after processing; results expire after 60 minutes.

## License

Copyright (c) 2026 GPTHub Tools contributors.

This project is licensed under the GNU Affero General Public License v3.0 or later. See [LICENSE](LICENSE). The network source requirement is intentional because PDF-to-DOCX uses PyMuPDF.
