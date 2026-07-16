from __future__ import annotations

import asyncio
from collections.abc import Sequence
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


def _ordered_required_detail_candidates(
    storage: Storage,
    fiction_ids: Sequence[str],
    backlog_limit: int,
) -> list[dict]:
    ordered_ids = list(dict.fromkeys(str(fiction_id) for fiction_id in fiction_ids))
    explicit_rows: list[dict] = []
    backlog_rows: list[dict] = []

    with storage.connect() as conn:
        if ordered_ids:
            placeholders = ",".join("?" for _ in ordered_ids)
            rows = conn.execute(
                f"""
                SELECT fiction_id, url, first_seen_utc
                FROM fiction
                WHERE fiction_id IN ({placeholders})
                """,
                ordered_ids,
            ).fetchall()
            by_id = {str(row["fiction_id"]): dict(row) for row in rows}
            explicit_rows = [
                by_id[fiction_id]
                for fiction_id in ordered_ids
                if fiction_id in by_id
            ]

        if backlog_limit > 0:
            rows = conn.execute(
                """
                SELECT f.fiction_id, f.url, f.first_seen_utc
                FROM fiction AS f
                WHERE f.first_seen_source='newest'
                  AND NOT EXISTS (
                    SELECT 1
                    FROM metric_observation AS mo
                    WHERE mo.fiction_id=f.fiction_id
                      AND mo.source_name='fiction_detail'
                      AND mo.followers IS NOT NULL
                      AND mo.total_views IS NOT NULL
                      AND mo.chapter_count IS NOT NULL
                  )
                ORDER BY f.first_seen_utc DESC, f.fiction_id DESC
                LIMIT ?
                """,
                (backlog_limit + len(ordered_ids),),
            ).fetchall()
            explicit_set = set(ordered_ids)
            backlog_rows = [
                dict(row)
                for row in rows
                if str(row["fiction_id"]) not in explicit_set
            ][:backlog_limit]

    return [
        {
            **row,
            "priority": 200,
            "required_initial_detail": True,
            "current_launch": True,
        }
        for row in explicit_rows
    ] + [
        {
            **row,
            "priority": 190,
            "required_initial_detail": True,
            "current_launch": False,
        }
        for row in backlog_rows
    ]


def _build_detail_plan(
    required_candidates: Sequence[dict],
    regular_candidates: Sequence[dict],
    detail_limit: int,
) -> list[dict]:
    plan: list[dict] = []
    seen: set[str] = set()

    for candidate in required_candidates:
        fiction_id = str(candidate["fiction_id"])
        if fiction_id in seen:
            continue
        plan.append({**candidate, "required_initial_detail": True})
        seen.add(fiction_id)

    target_count = max(detail_limit, len(plan))
    for candidate in regular_candidates:
        if len(plan) >= target_count:
            break
        fiction_id = str(candidate["fiction_id"])
        if fiction_id in seen:
            continue
        plan.append({**candidate, "required_initial_detail": False})
        seen.add(fiction_id)

    return plan


def _missing_new_fiction_metrics(
    storage: Storage,
    run_id: int,
    fiction_ids: Sequence[str],
) -> list[str]:
    ordered_ids = list(dict.fromkeys(str(fiction_id) for fiction_id in fiction_ids))
    if not ordered_ids:
        return []

    placeholders = ",".join("?" for _ in ordered_ids)
    with storage.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT fiction_id
            FROM metric_observation
            WHERE run_id=?
              AND source_name='fiction_detail'
              AND fiction_id IN ({placeholders})
              AND followers IS NOT NULL
              AND total_views IS NOT NULL
              AND chapter_count IS NOT NULL
            """,
            (run_id, *ordered_ids),
        ).fetchall()

    covered = {str(row["fiction_id"]) for row in rows}
    return [fiction_id for fiction_id in ordered_ids if fiction_id not in covered]


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

        new_fiction_ids = list(
            dict.fromkeys(
                str(fiction_id)
                for fiction_id in ((frontier_summary or {}).get("new_fiction_ids") or [])
            )
        )
        detail_count = 0
        missing_new_fiction_details: list[str] = []

        if enrich_details:
            required_candidates = _ordered_required_detail_candidates(
                storage,
                new_fiction_ids,
                backlog_limit=settings.detail_limit_per_run,
            )
            regular_candidates = storage.detail_candidates(
                run_id,
                settings.detail_limit_per_run + len(required_candidates),
                settings.detail_refresh_hours,
                settings.new_fiction_detail_hours,
            )
            candidates = _build_detail_plan(
                required_candidates,
                regular_candidates,
                settings.detail_limit_per_run,
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
                    detail_warnings.append(
                        f"detail {candidate['fiction_id']}: {type(exc).__name__}: {exc}"
                    )

            required_detail_ids = [
                str(candidate["fiction_id"])
                for candidate in required_candidates
            ]
            missing_required_details = _missing_new_fiction_metrics(
                storage,
                run_id,
                required_detail_ids,
            )
            missing_required_set = set(missing_required_details)
            missing_new_fiction_details = [
                fiction_id
                for fiction_id in new_fiction_ids
                if fiction_id in missing_required_set
            ]
            if missing_required_details:
                source_errors.append(
                    "newest_detail_coverage: missing core launch metrics for "
                    + ",".join(missing_required_details)
                )
        else:
            required_candidates = []
            missing_required_details = []

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
        new_fiction_id_set = set(new_fiction_ids)
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
            "new_fiction_details_expected": len(new_fiction_ids),
            "new_fiction_details_enriched": (
                len(new_fiction_ids) - len(missing_new_fiction_details)
                if enrich_details
                else 0
            ),
            "new_fiction_details_missing": missing_new_fiction_details,
            "new_fiction_backfill_requested": sum(
                1
                for candidate in required_candidates
                if not candidate.get("current_launch", False)
            ),
            "new_fiction_backfill_missing": [
                fiction_id
                for fiction_id in missing_required_details
                if fiction_id not in new_fiction_id_set
            ],
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
