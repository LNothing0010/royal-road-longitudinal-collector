# Changelog

## 0.4.0 — 2026-07-14

- Replaced the fixed one-page Newest sample with a verified moving frontier that follows pagination until the prior fiction-ID anchor is reached.
- Added a hard page ceiling, explicit completeness result and non-advancing recovery behavior when the anchor cannot be found.
- Labelled as new only fiction positioned before the first prior anchor; unknown overlap rows are excluded from the prospective cohort.
- Added atomic `data/catalog_state.json` frontier/cursor persistence.
- Added a bounded daily historical catalog backfill with page overlap, last-page detection and repeated verification passes.
- Added `rrlab backfill-catalog` and `rrlab catalog-status`.
- Kept catalog-only runs separate from latest Rising Stars panel validation.
- Added shared workflow write-concurrency so hourly collection and daily backfill cannot write SQLite simultaneously.
- Added frontier and catalog regression tests, reports and 90-day artifacts.

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
