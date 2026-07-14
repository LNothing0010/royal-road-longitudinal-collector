from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from . import __version__
from .config import SOURCES, Settings
from .derive import derive_run
from .http_source import PublicHtmlClient
from .models import SourceSnapshot
from .parsers import parse_detail_html, parse_listing_html
from .storage import Storage
from .validation import validate_run


async def collect(settings: Settings | None = None, enrich_details: bool = True) -> dict:
    settings = settings or Settings()
    timestamp = datetime.now(timezone.utc).replace(microsecond=0)
    storage = Storage(settings.db_path, settings.raw_dir)
    run_id = storage.begin_run(timestamp, __version__)
    client = PublicHtmlClient(settings)
    source_results: list[SourceSnapshot] = []
    errors: list[str] = []
    try:
        for spec in SOURCES:
            try:
                fetched = await client.get(spec.url)
                snapshot = parse_listing_html(
                    fetched.text, spec, timestamp,
                    http_status=fetched.status_code,
                    fetch_seconds=fetched.elapsed_seconds,
                )
                storage.persist_source(run_id, snapshot, fetched.text if settings.save_raw_html else None)
                source_results.append(snapshot)
            except Exception as exc:  # keep the run append-only even if one source fails
                errors.append(f"{spec.name}: {type(exc).__name__}: {exc}")
                empty = SourceSnapshot(
                    run_timestamp_utc=timestamp,
                    source_name=spec.name,
                    source_family=spec.family,
                    source_url=spec.url,
                    expected_count=spec.expected_count,
                    observed_count=0,
                    complete=False if spec.expected_count is not None else None,
                    warnings=[errors[-1]],
                )
                storage.persist_source(run_id, empty)
                source_results.append(empty)

        detail_count = 0
        if enrich_details:
            candidates = storage.detail_candidates(
                run_id,
                settings.detail_limit_per_run,
                settings.detail_refresh_hours,
                settings.new_fiction_detail_hours,
            )
            for candidate in candidates:
                try:
                    fetched = await client.get(candidate["url"])
                    detail = parse_detail_html(fetched.text, candidate["url"], timestamp)
                    storage.persist_detail(run_id, detail, fetched.text if settings.save_raw_html else None)
                    detail_count += 1
                except Exception as exc:
                    errors.append(f"detail {candidate['fiction_id']}: {type(exc).__name__}: {exc}")

        derive_run(settings.db_path, run_id)
        status = "complete" if not errors and all(s.complete is not False for s in source_results if s.expected_count is not None) else "partial"
        storage.finish_run(run_id, status, "\n".join(errors) if errors else None)
        validation_path = validate_run(settings.db_path, run_id, settings.report_dir)
        return {
            "run_id": run_id,
            "timestamp_utc": timestamp.isoformat(),
            "status": status,
            "sources": [{"name": s.source_name, "count": s.observed_count, "complete": s.complete, "warnings": s.warnings} for s in source_results],
            "details_enriched": detail_count,
            "errors": errors,
            "validation_report": str(validation_path),
        }
    except Exception as exc:
        storage.finish_run(run_id, "failed", f"{type(exc).__name__}: {exc}")
        raise
    finally:
        await client.close()


def run_collection(settings: Settings | None = None, enrich_details: bool = True) -> dict:
    return asyncio.run(collect(settings, enrich_details))
