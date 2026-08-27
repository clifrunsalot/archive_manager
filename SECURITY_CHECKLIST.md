# Sensitive Document Security Checklist

Use this checklist before ingesting personal or sensitive documentation.

## Completed

- [x] Redact OCR pages, prompts, evidence, answers, records, and source lists from trace logs by default.
  - Validation: `tests.lifecycle.test_trace_log`
  - Debug opt-in: `ARCHIVE_TRACE_CONTENT=1`
- [x] Add strict authorization that denies unmanifested sources by default.
  - Validation: `tests.security.test_access_policy`
  - Enable with: `ARCHIVE_AUTH_MODE=strict`
- [x] Require encryption, Qdrant authentication, and local service URLs in sensitive mode.
  - Validation: `tests.security.test_security_config`
  - Active by default (`ARCHIVE_SECURITY_MODE=sensitive`); disable with `source scripts/sensitive-off.sh`
  - Auto-generated key: `.archive_key` (or `ARCHIVE_ENCRYPTION_KEY`), `QDRANT_API_KEY` default in `settings.env`

## Remaining

- [x] Add confirmation-gated cleanup for existing sensitive logs and reports.
  - Run: `./scripts/secure-cleanup.sh`
  - Use `--force` only after confirming the files can be permanently deleted.
- [x] Add verified retention enforcement.
  - Test: expired events are excluded from queries.
  - Test: scheduled cleanup removes expired derived artifacts.
- [x] Add verified deletion for local source, derived, and cache storage.
  - Test: deletion fails if local derived data remains.
- [ ] Verify deletion across Qdrant and backup storage.
  - Test: deletion removes source files, OCR sidecars, searchable text, cache entries, manifests, Qdrant points, logs, and reports.
- [x] Add owner-only filesystem permission utility.
  - Run: `./scripts/secure-permissions.sh`
  - Verify sensitive directories are mode `700` and sensitive files are mode `600`.
  - Verify sensitive directories are mode `700` and sensitive files are mode `600`.
- [x] Minimize sensitive metadata in Qdrant payloads.
  - Sensitive mode omits optional `subject_ref`; event ID/type and source text remain for authorization and retrieval.
  - Validation: `tests.ingestion.test_ingest`
- [x] Add security regression tests.
  - Covers authorization, encryption configuration, logging, retention, deletion, endpoint restrictions, metadata exposure, and entry-point blocking.
  - Full validation: `135 tests OK`
- [ ] Execute cleanup and complete the final security review before ingesting real personal records.

## Operating Rules

- Keep `ARCHIVE_AUTH_MODE=strict` enabled for personal documents.
- Set a real `ARCHIVE_AUDIT_USER`; do not rely on the default `local-user` identity.
- Keep `ARCHIVE_TRACE_CONTENT` unset or set to `0`.
- Keep Qdrant, Ollama, and OCR ports bound to `127.0.0.1`.
- Use full-disk encryption and protect Docker storage on the host.
- Run the full test suite after each security change.
