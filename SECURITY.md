# Security policy

## Reporting a vulnerability

Do not open a public issue for a vulnerability that could expose user files or server access. Report it privately to `support@gpthub.ru` with the affected component, reproduction steps, and impact.

## File-handling guarantees

- The service uses allowlists, signature checks, ClamAV scanning, random server-side names, job capability tokens, and restricted worker containers.
- User filenames and file contents are not written to application logs.
- Inputs are removed after processing. Outputs expire after 60 minutes.
- Password-protected PDFs and scanned PDF-to-DOCX requests are rejected.

## Public repository boundary

- Commit only templates such as `.env.example`; never commit a runtime `.env`, certificates, private keys, capability tokens, upload data, logs or backups.
- AI model weights and model caches are intentionally ignored. Obtain them from their upstream projects and review their separate licenses before redistribution.
- Keep AI services on a private network. If a reverse proxy is necessary, do not add credentials to upstream URLs and restrict the proxy by network policy.

No upload service can promise that untrusted files are risk-free. Keep the runtime and conversion dependencies patched, and never mount the Docker socket into application containers.
