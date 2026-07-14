from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from . import __version__
from .catalog import (
    collect_newest_frontier,
    persist_frontier_state,
    write_frontier_report,
)
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
    source_errors: list[str] = []
    detail_warnings: list[str] = []
    frontier_summary: dict | None = None
    try:
        for spec in SOURCES:
            try:
                if spec.name == "newest":
                    frontier = await collect_newest_frontier(
                        client, storage, settings, timestamp
                    )
                    snapshot = frontier.snapshot
                    frontier_summary = frontier.summary
                    storage.persist_source(run_id, snapshot, None)
                    persist_frontier_state(settings, frontier_summary)
                    write_frontier_report(settings, run_id, frontier_summary)
                else:
                    fetched = await client.get(spec.url)
                    snapshot = parse_listing_html(
                        fetched.text,
                        spec,
                        timestamp,
                        http_status=fetched.status_code,
                        fetch_seconds=fetched.elapsed_seconds,
                    )
                    storage.persist_source(
                        run_id,
                        snapshot,
                        fetched.text if settings.save_raw_html else None,
                    )
                source_results.append(snapshot)
            except Exception as exc:  # keep the run append-only even if one source fails
                message = f"{spec.name}: {type(exc).__name__}: {exc}"
                source_errors.append(message)
                empty = SourceSnapshot(
                    run_timestamp_utc=timestamp,
                    source_name=spec.name,
                    source_family=spec.family,
                    source_url=spec.url,
                    expected_count=spec.expected_count,
                    observed_count=0,
                    complete=False if spec.expected_count is not None else None,
                    warnings=[message],
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
                    storage.persist_detail(
                        run_id,
                        detail,
                        fetched.text if settings.save_raw_html else None,
                    )
                    detail_count += 1
                except Exception as exc:
                    # Detail enrichment is optional. A malformed or temporarily changed
                    # detail page must not invalidate a complete six-list RS snapshot.
                    detail_warnings.append(
                        f"detail {candidate['fiction_id']}: {type(exc).__name__}: {exc}"
                    )

        derive_run(settings.db_path, run_id)
        required_sources_complete = all(
            snapshot.complete is not False
            for snapshot in source_results
            if snapshot.expected_count is not None
        )
        status = (
            "complete"
            if not source_errors and required_sources_complete
            else "partial"
        )
        notes = "\n".join([*source_errors, *detail_warnings]) or None
        storage.finish_run(run_id, status, notes)
        validation_path = validate_run(settings.db_path, run_id, settings.report_dir)
        return {
            "run_id": run_id,
            "timestamp_utc": timestamp.isoformat(),
            "status": status,
            "sources": [
                {
                    "name": snapshot.source_name,
                    "count": snapshot.observed_count,
                    "complete": snapshot.complete,
                    "warnings": snapshot.warnings,
                }
                for snapshot in source_results
            ],
            "newest_frontier": frontier_summary,
            "details_enriched": detail_count,
            "errors": source_errors,
            "warnings": detail_warnings,
            "validation_report": str(validation_path),
        }
    except Exception as exc:
        storage.finish_run(run_id, "failed", f"{type(exc).__name__}: {exc}")
        raise
    finally:
        await client.close()


def run_collection(settings: Settings | None = None, enrich_details: bool = True) -> dict:
    return asyncio.run(collect(settings, enrich_details))
