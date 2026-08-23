# Word and spreadsheet conversions

GPTHub Tools provides two structural Office conversions in addition to the existing Word-to-PDF and PDF-to-Word operations. They run locally in the normal worker containers; neither the source document nor the result is sent to an external conversion service.

## Operations and formats

| Operation | Endpoint | Accepted input | Result |
| --- | --- | --- | --- |
| Word to Excel | `POST /api/jobs/word-to-excel` | DOC, DOCX, ODT, RTF | XLSX, or ZIP for multiple inputs |
| Excel to Word | `POST /api/jobs/excel-to-word` | XLS, XLSX, ODS, CSV | DOCX, or ZIP for multiple inputs |

Both endpoints use the standard asynchronous job API. Send one or more multipart `files` fields and a JSON object in the multipart `options` field. The response contains the job ID, capability token and expiry time; use the normal job status and download endpoints described in the main [README](../README.md#api).

The browser interface exposes the operations at `/convert/documents/word-to-excel` and `/convert/documents/excel-to-word`. The existing `/convert/documents/word-to-pdf` and `/convert/documents/pdf-to-word` routes and behavior remain unchanged.

Example:

```bash
curl --fail --request POST http://localhost:9080/api/jobs/excel-to-word \
  --form 'files=@report.csv' \
  --form 'options={"locale":"en","csvDelimiter":"semicolon","csvEncoding":"utf-8"}'
```

## Options

| Option | Operations | Allowed values | Meaning |
| --- | --- | --- | --- |
| `locale` | Both | `ru`, `en` | Language for generated labels. It does not translate document contents. |
| `csvDelimiter` | Excel to Word, CSV only | `auto`, `comma`, `semicolon`, `tab`, `pipe` | Delimiter used to parse CSV. `auto` detects it from the input. |
| `csvEncoding` | Excel to Word, CSV only | `auto`, `utf-8`, `windows-1251` | Character encoding used to decode CSV. `auto` detects it from the input. |

The CSV-specific options are ignored for XLS, XLSX and ODS inputs. Automatic CSV detection is convenient, but an explicit delimiter and encoding are preferable for ambiguous files.

## Conversion behavior and limitations

Word-to-Excel creates one worksheet per input document. Main text, paragraphs, lists, images and tables are transferred into a single-sheet, row-oriented layout in source order. It is intended for extracting editable content and tabular structure, not for reproducing a Word page pixel for pixel. Unsupported floating or embedded objects, text boxes, advanced fields, headers and footers, and complex page layout may be skipped or simplified.

Excel-to-Word renders each non-empty worksheet as a Word table. Formula cells use the values saved in the workbook; GPTHub Tools does not run Excel's calculation engine. Recalculate and save the workbook before uploading if current formula results matter. Charts, drawings, embedded objects, macros, conditional formatting and advanced spreadsheet layout are not reproduced. Very wide or large sheets may be simplified or rejected by the complexity limits.

Legacy DOC and XLS inputs are normalized with headless LibreOffice before structural conversion. LibreOffice compatibility is good but not identical to Microsoft Office, so fonts, pagination, spacing, merged cells and other formatting can change. PDF-to-Word remains a separate operation and still requires a text layer; OCR is out of scope.

Successful jobs may include these stable warning codes:

| Warning | Meaning |
| --- | --- |
| `FORMULAS_AS_VALUES` | Spreadsheet formulas were represented by saved displayed values, or by formula text when no saved value existed. |
| `OBJECTS_SKIPPED` | Unsupported auxiliary content, embedded objects or drawings were omitted. |
| `LAYOUT_SIMPLIFIED` | Source formatting or layout could not be represented exactly in the target format. |
| `CSV_DETECTION_GUESSED` | At least one CSV setting was inferred; choose explicit settings if the result is incorrect. |

Warnings are returned in the successful job status response. They describe a usable best-effort result and do not by themselves make the job fail. Review the downloaded document before relying on it for publishing, records or calculations.

## Errors

| Error | Meaning |
| --- | --- |
| `OFFICE_PASSWORD_PROTECTED` | The input is encrypted or password-protected. Remove protection locally and upload it again. Passwords are not accepted by the API. |
| `OFFICE_MACROS_NOT_ALLOWED` | A macro was detected in the input. Macro-bearing Office files are rejected rather than executed or copied. |
| `OFFICE_TOO_COMPLEX` | The document exceeds structural or resource limits for safe conversion. Split or simplify it and retry. |
| `OFFICE_CONVERSION_FAILED` | LibreOffice or the structural converter could not produce a valid result. |
| `CSV_PARSE_FAILED` | The CSV could not be decoded or parsed with the selected settings. Retry with explicit delimiter and encoding values. |

## Security notes

- Uploaded Office files pass the same filename, extension, signature, size and ClamAV checks as other GPTHub Tools jobs.
- Encrypted documents are rejected. The service does not collect, log or retain Office passwords.
- Macro-bearing legacy and Open XML Office files are rejected before conversion. Macros are never executed.
- Open XML packages are inspected with bounded archive handling and hardened XML parsing. Files that exceed the safe structural limits fail with `OFFICE_TOO_COMPLEX`.
- LibreOffice and the conversion libraries run inside the unprivileged, read-only local worker containers with temporary storage separated from retained job results. Those workers attach only to a Docker `internal: true` network: they can reach Redis and ClamAV, but have no internet egress.
- Output conversion is not a forensic sanitization guarantee. Treat untrusted downloads according to your normal document-security policy and inspect warnings before use.

Inputs are deleted when processing finishes or fails. Results are protected by the job capability token and expire after 60 minutes, as described in the [privacy notice](PRIVACY.md).
