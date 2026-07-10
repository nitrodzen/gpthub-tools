# Security policy

## Reporting a vulnerability

Do not open a public issue for a vulnerability that could expose user files or server access. Report it privately to `support@gpthub.ru` with the affected component, reproduction steps, and impact.

## File-handling guarantees

- The service uses allowlists, signature checks, ClamAV scanning, random server-side names, job capability tokens, and restricted worker containers.
- User filenames and file contents are not written to application logs.
- Inputs are removed after processing. Outputs expire after 60 minutes.
- Password-protected PDFs and scanned PDF-to-DOCX requests are rejected.

No upload service can promise that untrusted files are risk-free. Keep the runtime and conversion dependencies patched, and never mount the Docker socket into application containers.
