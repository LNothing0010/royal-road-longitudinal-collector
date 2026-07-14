# Changelog

## 0.3.1 — 2026-07-14

- Fixed punctuation-only Royal Road metric placeholders such as `.` being parsed as floats.
- Separated optional detail-enrichment warnings from core list-collection failures.
- Replaced schema-coupled inline workflow SQL with the collector's versioned validator.
- Persisted partial runs and diagnostics even when validation fails, preventing silent data loss.
- Upgraded artifact upload to the Node 24-compatible official action major.

## 0.3.0 — 2026-07-14

- Removed the ZIP/bootstrap installation path entirely; source files are committed directly.
- Replaced Node 20-based checkout/setup actions with Node 24-compatible major versions.
- Added deterministic preflight checks (`rrlab doctor`), compilation, tests and Ruff validation.
- Declared test dependencies in the hourly workflow instead of assuming `pytest` exists.
- Disabled undeclared HTTP/2 support in the HTTP client.
- Made settings read and validate environment variables at instantiation time.
- Added resilient Git persistence with bounded retry/rebase handling.
- Added always-on validation summaries and 90-day diagnostic artifacts.
- Kept partial observations rather than fabricating missing rows.
