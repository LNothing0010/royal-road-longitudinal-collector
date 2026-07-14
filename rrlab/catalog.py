from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from . import __version__
from .config import CATALOG_BACKFILL_SOURCE, SOURCE_MAP, Settings, SourceSpec
from .http_source import PublicHtmlClient
from .models import ReleaseObservation, SourceSnapshot
from .parsers import parse_listing_html
from .storage import Storage

PAGE_SIZE = 20
STATE_VERSION = 1


@dataclass(frozen=True)
class FrontierResult:
    snapshot: SourceSnapshot
    summary: dict[str, Any]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _page_url(base_url: str, page: int) -> str:
    parts = urlsplit(base_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["page"] = str(page)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _last_page_from_html(html: str) -> int | None:
    soup = BeautifulSoup(html, "lxml")
    pages: list[int] = []
    for anchor in soup.select('a[href*="page="]'):
        href = str(anchor.get("href", ""))
        query = dict(parse_qsl(urlsplit(href).query, keep_blank_values=True))
        raw = query.get("page")
        if raw and raw.isdigit():
            pages.append(int(raw))
    return max(pages) if pages else None


def _read_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": STATE_VERSION, "backfill_next_page": 2, "backfill_pass": 1}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read catalog state {path}: {exc}") from exc
    if payload.get("version") != STATE_VERSION:
        raise RuntimeError(
            f"Unsupported catalog state version {payload.get('version')!r}; expected {STATE_VERSION}"
        )
    return payload


def _write_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {**payload, "version": STATE_VERSION, "updated_utc": _utc_now()}
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _known_fiction_ids(storage: Storage) -> set[str]:
    storage.init()
    with storage.connect() as conn:
        return {str(row[0]) for row in conn.execute("SELECT fiction_id FROM fiction")}


def _latest_newest_anchor_ids(storage: Storage, limit: int) -> list[str]:
    storage.init()
    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT lm.fiction_id
            FROM listing_membership lm
            WHERE lm.source_name='newest'
              AND lm.run_id=(
                SELECT MAX(run_id) FROM listing_membership WHERE source_name='newest'
              )
            ORDER BY lm.rank
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [str(row[0]) for row in rows]


def _merge_snapshots(
    page_snapshots: list[tuple[int, SourceSnapshot]],
    spec: SourceSpec,
    timestamp: datetime,
    complete: bool | None,
    warnings: list[str],
) -> SourceSnapshot:
    observations = []
    releases: list[ReleaseObservation] = []
    seen_fictions: set[str] = set()
    seen_releases: set[tuple[str, str]] = set()
    elapsed = 0.0
    status_codes: list[int] = []

    for page, snapshot in page_snapshots:
        elapsed += snapshot.fetch_seconds or 0.0
        if snapshot.http_status is not None:
            status_codes.append(snapshot.http_status)
        for warning in snapshot.warnings:
            warnings.append(f"page={page}: {warning}")
        for observation in snapshot.observations:
            if observation.fiction_id in seen_fictions:
                continue
            seen_fictions.add(observation.fiction_id)
            local_rank = observation.rank or 1
            observations.append(
                observation.model_copy(
                    update={
                        "source_name": spec.name,
                        "source_family": spec.family,
                        "rank": (page - 1) * PAGE_SIZE + local_rank,
                    }
                )
            )
        for release in snapshot.releases:
            release_key = release.chapter_id or release.chapter_url or release.chapter_title
            key = (release.fiction_id, release_key)
            if key in seen_releases:
                continue
            seen_releases.add(key)
            releases.append(release.model_copy(update={"source_name": spec.name}))

    http_status = status_codes[-1] if status_codes else None
    if status_codes and any(code != status_codes[0] for code in status_codes):
        warnings.append(f"mixed_http_statuses={sorted(set(status_codes))}")

    return SourceSnapshot(
        run_timestamp_utc=timestamp,
        source_name=spec.name,
        source_family=spec.family,
        source_url=spec.url,
        expected_count=spec.expected_count,
        observed_count=len(observations),
        complete=complete,
        observations=observations,
        releases=releases,
        warnings=warnings,
        http_status=http_status,
        fetch_seconds=elapsed,
    )


async def collect_newest_frontier(
    client: PublicHtmlClient,
    storage: Storage,
    settings: Settings,
    timestamp: datetime,
) -> FrontierResult:
    spec = SOURCE_MAP["newest"]
    state = _read_state(settings.catalog_state_path)
    anchor_ids = [str(value) for value in state.get("frontier_anchor_ids", [])]
    if not anchor_ids:
        anchor_ids = _latest_newest_anchor_ids(storage, settings.frontier_anchor_limit)
    anchor_set = set(anchor_ids)
    known_before = _known_fiction_ids(storage)

    page_snapshots: list[tuple[int, SourceSnapshot]] = []
    boundary_page: int | None = None
    boundary_index: int | None = None
    pages_after_boundary = 0
    candidate_ids: list[str] = []
    persist_ids: list[str] = []
    overlap_ids: list[str] = []
    warnings: list[str] = []

    for page in range(1, settings.newest_max_pages + 1):
        fetched = await client.get(_page_url(spec.url, page))
        snapshot = parse_listing_html(
            fetched.text,
            spec,
            timestamp,
            http_status=fetched.status_code,
            fetch_seconds=fetched.elapsed_seconds,
        )
        if snapshot.observed_count == 0:
            warnings.append(f"frontier_empty_page={page}")
            break
        page_snapshots.append((page, snapshot))
        page_ids = [item.fiction_id for item in snapshot.observations]

        if boundary_page is None:
            anchor_positions = [
                index for index, fiction_id in enumerate(page_ids) if fiction_id in anchor_set
            ]
            if anchor_positions:
                boundary_page = page
                boundary_index = min(anchor_positions)
                candidate_ids.extend(page_ids[:boundary_index])
                # Preserve every candidate before the first prior anchor, plus rows that
                # were already known. Unknown rows after the anchor belong to the overlap
                # region and must not be mislabeled as newly launched fiction.
                persist_ids.extend(page_ids[:boundary_index])
                persist_ids.extend(
                    fiction_id
                    for fiction_id in page_ids[boundary_index:]
                    if fiction_id in known_before
                )
                overlap_ids.extend(
                    fiction_id
                    for fiction_id in page_ids[boundary_index + 1 :]
                    if fiction_id not in known_before
                )
                pages_after_boundary = 0
            else:
                candidate_ids.extend(page_ids)
                persist_ids.extend(page_ids)
        else:
            overlap_ids.extend(page_ids)
            pages_after_boundary += 1

        if boundary_page is not None and pages_after_boundary >= settings.frontier_overlap_pages:
            break
        if not anchor_set:
            # A new installation has no previous frontier. Establish a one-page baseline;
            # the next run can prove prospective continuity against these anchors.
            persist_ids = page_ids
            break

    boundary_reached = boundary_page is not None
    all_scanned_ids = [
        observation.fiction_id
        for _, snapshot in page_snapshots
        for observation in snapshot.observations
    ]
    unique_scanned_ids = list(dict.fromkeys(all_scanned_ids))
    unique_persist_ids = list(dict.fromkeys(persist_ids))
    persist_set = set(unique_persist_ids)
    unique_candidate_ids = list(dict.fromkeys(candidate_ids))
    new_ids = (
        [fiction_id for fiction_id in unique_candidate_ids if fiction_id not in known_before]
        if anchor_set
        else []
    )
    overlap_unknown_ids = [
        fiction_id
        for fiction_id in dict.fromkeys(overlap_ids)
        if fiction_id not in known_before and fiction_id not in persist_set
    ]
    max_exhausted = (
        bool(anchor_set)
        and not boundary_reached
        and len(page_snapshots) >= settings.newest_max_pages
    )

    if not anchor_set:
        warnings.append("frontier_baseline_initialized_without_prior_anchor")
    if max_exhausted:
        warnings.append(
            f"frontier_anchor_not_reached_within_{settings.newest_max_pages}_pages"
        )

    coverage_complete = boundary_reached and not max_exhausted
    merged = _merge_snapshots(
        page_snapshots,
        spec,
        timestamp,
        complete=coverage_complete if anchor_set else None,
        warnings=warnings,
    )
    if anchor_set:
        filtered_observations = [
            item for item in merged.observations if item.fiction_id in persist_set
        ]
        filtered_fiction_ids = {item.fiction_id for item in filtered_observations}
        filtered_releases = [
            item for item in merged.releases if item.fiction_id in filtered_fiction_ids
        ]
        merged = merged.model_copy(
            update={
                "observed_count": len(filtered_observations),
                "observations": filtered_observations,
                "releases": filtered_releases,
            }
        )

    next_anchor_ids = unique_persist_ids[: settings.frontier_anchor_limit]
    summary = {
        "mode": "prospective_newest_census",
        "timestamp_utc": timestamp.isoformat(),
        "pages_fetched": len(page_snapshots),
        "first_page": page_snapshots[0][0] if page_snapshots else None,
        "last_page": page_snapshots[-1][0] if page_snapshots else None,
        "boundary_page": boundary_page,
        "boundary_index": boundary_index,
        "boundary_reached": boundary_reached,
        "coverage_complete": coverage_complete,
        "max_pages": settings.newest_max_pages,
        "overlap_pages_after_anchor": settings.frontier_overlap_pages,
        "anchor_count_before": len(anchor_ids),
        "scanned_unique_fictions": len(unique_scanned_ids),
        "observed_unique_fictions": merged.observed_count,
        "new_fictions": len(new_ids),
        "new_fiction_ids": new_ids,
        "overlap_unknown_fictions_excluded": len(overlap_unknown_ids),
        "next_anchor_ids": next_anchor_ids,
        "initialize_anchor": not anchor_set and bool(next_anchor_ids),
        "warnings": warnings,
    }
    return FrontierResult(snapshot=merged, summary=summary)


def persist_frontier_state(settings: Settings, summary: dict[str, Any]) -> None:
    should_update = bool(summary.get("coverage_complete") or summary.get("initialize_anchor"))
    if not should_update:
        return
    state = _read_state(settings.catalog_state_path)
    state["frontier_anchor_ids"] = list(summary.get("next_anchor_ids", []))
    state["frontier_last_run_utc"] = summary.get("timestamp_utc")
    state["frontier_last_complete"] = bool(summary.get("coverage_complete"))
    state["frontier_last_pages_fetched"] = summary.get("pages_fetched")
    _write_state(settings.catalog_state_path, state)


def write_frontier_report(
    settings: Settings, run_id: int, summary: dict[str, Any]
) -> Path:
    settings.report_dir.mkdir(parents=True, exist_ok=True)
    payload = {**summary, "run_id": run_id}
    path = settings.report_dir / f"frontier_run_{run_id}.json"
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    (settings.report_dir / "frontier_latest.json").write_text(text, encoding="utf-8")
    return path


async def backfill_catalog(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or Settings()
    timestamp = datetime.now(timezone.utc).replace(microsecond=0)
    storage = Storage(settings.db_path, settings.raw_dir)
    storage.init()
    state = _read_state(settings.catalog_state_path)
    next_page = max(2, int(state.get("backfill_next_page", 2)))
    pass_number = max(1, int(state.get("backfill_pass", 1)))
    start_page = max(2, next_page - settings.backfill_overlap_pages)
    planned_end_page = next_page + settings.backfill_pages_per_run - 1
    known_before = _known_fiction_ids(storage)

    run_id = storage.begin_run(timestamp, __version__)
    client = PublicHtmlClient(settings)
    page_snapshots: list[tuple[int, SourceSnapshot]] = []
    warnings: list[str] = []
    last_page_seen: int | None = None
    reached_end = False

    try:
        for page in range(start_page, planned_end_page + 1):
            fetched = await client.get(_page_url(CATALOG_BACKFILL_SOURCE.url, page))
            parsed_last_page = _last_page_from_html(fetched.text)
            if parsed_last_page is not None:
                last_page_seen = max(last_page_seen or 0, parsed_last_page)
            snapshot = parse_listing_html(
                fetched.text,
                CATALOG_BACKFILL_SOURCE,
                timestamp,
                http_status=fetched.status_code,
                fetch_seconds=fetched.elapsed_seconds,
            )
            if snapshot.observed_count == 0:
                warnings.append(f"backfill_empty_page={page}")
                reached_end = True
                break
            page_snapshots.append((page, snapshot))
            if last_page_seen is not None and page >= last_page_seen:
                reached_end = True
                break

        if not page_snapshots:
            raise RuntimeError("Catalog backfill returned no fiction pages")

        merged = _merge_snapshots(
            page_snapshots,
            CATALOG_BACKFILL_SOURCE,
            timestamp,
            complete=reached_end,
            warnings=warnings,
        )
        storage.persist_source(run_id, merged)
        storage.finish_run(run_id, "complete" if merged.observed_count else "partial")

        observed_ids = [item.fiction_id for item in merged.observations]
        new_ids = [fiction_id for fiction_id in observed_ids if fiction_id not in known_before]
        actual_end_page = page_snapshots[-1][0]

        if reached_end:
            state["backfill_passes_completed"] = int(
                state.get("backfill_passes_completed", 0)
            ) + 1
            state["backfill_pass"] = pass_number + 1
            state["backfill_next_page"] = 2
            state["catalog_complete_once"] = True
            state["catalog_last_completed_utc"] = timestamp.isoformat()
        else:
            state["backfill_pass"] = pass_number
            state["backfill_next_page"] = actual_end_page + 1
        state["backfill_last_page_seen"] = last_page_seen
        state["backfill_last_run_utc"] = timestamp.isoformat()
        state["backfill_last_start_page"] = start_page
        state["backfill_last_end_page"] = actual_end_page
        state["backfill_last_new_fictions"] = len(new_ids)
        _write_state(settings.catalog_state_path, state)

        report = {
            "run_id": run_id,
            "timestamp_utc": timestamp.isoformat(),
            "mode": "historical_catalog_backfill",
            "pass": pass_number,
            "start_page": start_page,
            "end_page": actual_end_page,
            "pages_fetched": len(page_snapshots),
            "planned_new_pages": settings.backfill_pages_per_run,
            "overlap_pages": settings.backfill_overlap_pages,
            "last_page_seen": last_page_seen,
            "reached_catalog_end": reached_end,
            "observed_unique_fictions": merged.observed_count,
            "new_fictions": len(new_ids),
            "new_fiction_ids": new_ids,
            "next_page": state["backfill_next_page"],
            "catalog_complete_once": bool(state.get("catalog_complete_once")),
            "warnings": warnings,
        }
        settings.report_dir.mkdir(parents=True, exist_ok=True)
        text = json.dumps(report, indent=2, ensure_ascii=False)
        (settings.report_dir / f"catalog_backfill_run_{run_id}.json").write_text(
            text, encoding="utf-8"
        )
        (settings.report_dir / "catalog_backfill_latest.json").write_text(
            text, encoding="utf-8"
        )
        return report
    except Exception as exc:
        storage.finish_run(run_id, "failed", f"{type(exc).__name__}: {exc}")
        raise
    finally:
        await client.close()


def run_catalog_backfill(settings: Settings | None = None) -> dict[str, Any]:
    return asyncio.run(backfill_catalog(settings))


def catalog_status(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or Settings()
    storage = Storage(settings.db_path, settings.raw_dir)
    storage.init()
    state = _read_state(settings.catalog_state_path)
    with storage.connect() as conn:
        total_fictions = int(conn.execute("SELECT COUNT(*) FROM fiction").fetchone()[0])
        catalog_fictions = int(
            conn.execute(
                """
                SELECT COUNT(DISTINCT fiction_id)
                FROM listing_membership
                WHERE source_name='catalog_backfill'
                """
            ).fetchone()[0]
        )
        prospective_fictions = int(
            conn.execute(
                """
                SELECT COUNT(DISTINCT fiction_id)
                FROM listing_membership
                WHERE source_name='newest'
                """
            ).fetchone()[0]
        )
        latest_panel = conn.execute(
            """
            SELECT MAX(r.run_id)
            FROM run r
            WHERE EXISTS (
                SELECT 1 FROM source_snapshot s
                WHERE s.run_id=r.run_id AND s.source_family='rising_stars'
            )
            """
        ).fetchone()[0]
    return {
        "generated_utc": _utc_now(),
        "total_registered_fictions": total_fictions,
        "historical_catalog_fictions": catalog_fictions,
        "prospectively_observed_newest_fictions": prospective_fictions,
        "latest_panel_run_id": latest_panel,
        "frontier_last_run_utc": state.get("frontier_last_run_utc"),
        "frontier_last_complete": state.get("frontier_last_complete"),
        "frontier_anchor_count": len(state.get("frontier_anchor_ids", [])),
        "backfill_pass": state.get("backfill_pass", 1),
        "backfill_next_page": state.get("backfill_next_page", 2),
        "backfill_last_page_seen": state.get("backfill_last_page_seen"),
        "backfill_passes_completed": state.get("backfill_passes_completed", 0),
        "catalog_complete_once": bool(state.get("catalog_complete_once", False)),
        "state_path": str(settings.catalog_state_path),
    }
