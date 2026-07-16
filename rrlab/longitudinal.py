from __future__ import annotations

import asyncio
import csv
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Sequence

from . import __version__
from .availability import (
    detail_retry_suppressed_ids,
    ensure_detail_fetch_state,
    record_detail_failure,
    record_detail_success,
)
from .config import Settings
from .derive import derive_run
from .http_source import PublicHtmlClient
from .launch_analysis import build_launch_analysis
from .parsers import parse_detail_html
from .storage import Storage

ANALYSIS_VERSION = "longitudinal-launch-v1"

LONGITUDINAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS longitudinal_tracking (
  fiction_id TEXT PRIMARY KEY,
  tracking_started_utc TEXT NOT NULL,
  tracking_reason TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  last_requested_utc TEXT,
  last_success_utc TEXT,
  request_count INTEGER NOT NULL DEFAULT 0,
  success_count INTEGER NOT NULL DEFAULT 0,
  failure_count INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY(fiction_id) REFERENCES fiction(fiction_id)
);

CREATE TABLE IF NOT EXISTS detail_fetch_observation (
  attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  fiction_id TEXT NOT NULL,
  attempted_utc TEXT NOT NULL,
  outcome TEXT NOT NULL,
  availability TEXT NOT NULL,
  http_status INTEGER,
  error_type TEXT,
  error_message TEXT,
  raw_json_path TEXT,
  UNIQUE(run_id, fiction_id),
  FOREIGN KEY(run_id) REFERENCES run(run_id),
  FOREIGN KEY(fiction_id) REFERENCES fiction(fiction_id)
);

CREATE TABLE IF NOT EXISTS analysis_observation (
  run_id INTEGER NOT NULL,
  fiction_id TEXT NOT NULL,
  analysis_version TEXT NOT NULL,
  observed_utc TEXT NOT NULL,
  latest_metric_run_id INTEGER,
  latest_metric_observed_utc TEXT,
  previous_metric_run_id INTEGER,
  previous_metric_observed_utc TEXT,
  elapsed_hours REAL,
  followers INTEGER,
  total_views INTEGER,
  chapter_count INTEGER,
  favorites INTEGER,
  rating_count INTEGER,
  follower_delta INTEGER,
  view_delta INTEGER,
  chapter_delta INTEGER,
  favorite_delta INTEGER,
  rating_count_delta INTEGER,
  follower_pct_change REAL,
  view_pct_change REAL,
  followers_per_day_increment REAL,
  views_per_day_increment REAL,
  current_rs INTEGER NOT NULL,
  current_best_rs_rank INTEGER,
  launch_index REAL,
  payload_json TEXT NOT NULL,
  PRIMARY KEY(run_id, fiction_id, analysis_version),
  FOREIGN KEY(run_id) REFERENCES run(run_id),
  FOREIGN KEY(fiction_id) REFERENCES fiction(fiction_id)
);

CREATE INDEX IF NOT EXISTS idx_longitudinal_tracking_active
ON longitudinal_tracking(active, tracking_started_utc);

CREATE INDEX IF NOT EXISTS idx_detail_fetch_observation_fiction
ON detail_fetch_observation(fiction_id, attempted_utc);

CREATE INDEX IF NOT EXISTS idx_analysis_observation_fiction
ON analysis_observation(fiction_id, observed_utc, analysis_version);
"""


def _utc_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _delta(current: Any, previous: Any) -> Any:
    if current is None or previous is None:
        return None
    return current - previous


def _pct_change(current: int | float | None, previous: int | float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return round((float(current) - float(previous)) / float(previous) * 100.0, 4)


def _rate_per_day(delta: int | float | None, elapsed_hours: float | None) -> float | None:
    if delta is None or elapsed_hours is None or elapsed_hours <= 0:
        return None
    return round(float(delta) / elapsed_hours * 24.0, 4)


def _env_nonnegative_int(name: str, default: int = 0) -> int:
    raw = os.getenv(name)
    value = default if raw in (None, "") else int(raw)
    if value < 0:
        raise ValueError(f"{name} must be >= 0")
    return value


def ensure_longitudinal_schema(storage: Storage) -> None:
    storage.init()
    ensure_detail_fetch_state(storage)
    with storage.connect() as conn:
        conn.executescript(LONGITUDINAL_SCHEMA)
        conn.execute(
            """
            INSERT OR IGNORE INTO model_registry(
              version,created_utc,status,parameters_json,notes
            ) VALUES(?,?,?,?,?)
            """,
            (
                ANALYSIS_VERSION,
                _utc_text(datetime.now(timezone.utc)),
                "active",
                json.dumps(
                    {
                        "raw_observations": "append_only",
                        "comparison": "latest_vs_previous_detail_observation",
                        "launch_index_source": "launch_analysis.py",
                        "views_are_outcomes": True,
                    },
                    sort_keys=True,
                ),
                "Versioned longitudinal snapshot and delta analysis.",
            ),
        )
        conn.commit()


def bootstrap_tracking(storage: Storage) -> int:
    """Track every prospectively discovered or previously detailed fiction."""
    ensure_longitudinal_schema(storage)
    with storage.connect() as conn:
        before = int(conn.execute("SELECT COUNT(*) FROM longitudinal_tracking").fetchone()[0])
        conn.execute(
            """
            INSERT OR IGNORE INTO longitudinal_tracking(
              fiction_id,tracking_started_utc,tracking_reason,active
            )
            SELECT f.fiction_id,f.first_seen_utc,
                   CASE
                     WHEN EXISTS(
                       SELECT 1 FROM metric_observation mo
                       WHERE mo.fiction_id=f.fiction_id
                         AND mo.source_name='fiction_detail'
                     ) THEN 'fiction_detail_observed'
                     ELSE 'newest_frontier'
                   END,
                   1
            FROM fiction f
            WHERE f.first_seen_source='newest'
               OR EXISTS(
                 SELECT 1 FROM listing_membership lm
                 WHERE lm.fiction_id=f.fiction_id
                   AND lm.source_name='newest'
               )
               OR EXISTS(
                 SELECT 1 FROM metric_observation mo
                 WHERE mo.fiction_id=f.fiction_id
                   AND mo.source_name='fiction_detail'
               )
            """
        )
        conn.commit()
        after = int(conn.execute("SELECT COUNT(*) FROM longitudinal_tracking").fetchone()[0])
    return after - before


def tracked_candidates(
    storage: Storage,
    *,
    now: datetime | None = None,
    max_fictions: int = 0,
) -> list[dict[str, Any]]:
    ensure_longitudinal_schema(storage)
    bootstrap_tracking(storage)
    current = now or datetime.now(timezone.utc)
    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT lt.fiction_id,f.title,f.author,f.url,f.first_seen_utc,
                   lt.tracking_started_utc,lt.last_requested_utc,
                   lt.request_count,lt.success_count,lt.failure_count
            FROM longitudinal_tracking lt
            JOIN fiction f USING(fiction_id)
            WHERE lt.active=1
            ORDER BY COALESCE(lt.last_requested_utc,''),
                     f.first_seen_utc DESC,
                     f.fiction_id DESC
            """
        ).fetchall()
    candidates = [dict(row) for row in rows]
    suppressed = detail_retry_suppressed_ids(
        storage,
        tuple(str(row["fiction_id"]) for row in candidates),
        current,
    )
    candidates = [
        row for row in candidates if str(row["fiction_id"]) not in suppressed
    ]
    if max_fictions > 0:
        candidates = candidates[:max_fictions]
    return candidates


def _record_attempt(
    storage: Storage,
    *,
    run_id: int,
    fiction_id: str,
    attempted_utc: datetime,
    outcome: str,
    availability: str,
    http_status: int | None,
    raw_json_path: str | None = None,
    error: Exception | None = None,
) -> None:
    ensure_longitudinal_schema(storage)
    success = outcome == "success"
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO detail_fetch_observation(
              run_id,fiction_id,attempted_utc,outcome,availability,http_status,
              error_type,error_message,raw_json_path
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id,
                str(fiction_id),
                _utc_text(attempted_utc),
                outcome,
                availability,
                http_status,
                type(error).__name__ if error else None,
                str(error)[:2000] if error else None,
                raw_json_path,
            ),
        )
        conn.execute(
            """
            UPDATE longitudinal_tracking
            SET last_requested_utc=?,
                last_success_utc=CASE WHEN ? THEN ? ELSE last_success_utc END,
                request_count=request_count+1,
                success_count=success_count+CASE WHEN ? THEN 1 ELSE 0 END,
                failure_count=failure_count+CASE WHEN ? THEN 0 ELSE 1 END
            WHERE fiction_id=?
            """,
            (
                _utc_text(attempted_utc),
                int(success),
                _utc_text(attempted_utc),
                int(success),
                int(success),
                str(fiction_id),
            ),
        )
        conn.commit()


def _metric_history(
    conn: sqlite3.Connection,
    fiction_id: str,
    run_id: int,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT mo.*,r.timestamp_utc
        FROM metric_observation mo
        JOIN run r USING(run_id)
        WHERE mo.fiction_id=?
          AND mo.source_name='fiction_detail'
          AND mo.run_id<=?
        ORDER BY r.timestamp_utc,mo.run_id
        """,
        (str(fiction_id), run_id),
    ).fetchall()
    return [dict(row) for row in rows]


def _augment_with_previous(
    row: dict[str, Any],
    history: Sequence[dict[str, Any]],
    analysis_run_id: int,
) -> dict[str, Any]:
    latest = history[-1] if history else None
    previous = history[-2] if len(history) >= 2 else None
    latest_utc = _parse_utc(latest.get("timestamp_utc")) if latest else None
    previous_utc = _parse_utc(previous.get("timestamp_utc")) if previous else None
    elapsed_hours = (
        (latest_utc - previous_utc).total_seconds() / 3600.0
        if latest_utc and previous_utc
        else None
    )
    follower_delta = _delta(
        latest.get("followers") if latest else None,
        previous.get("followers") if previous else None,
    )
    view_delta = _delta(
        latest.get("total_views") if latest else None,
        previous.get("total_views") if previous else None,
    )
    chapter_delta = _delta(
        latest.get("chapter_count") if latest else None,
        previous.get("chapter_count") if previous else None,
    )
    favorite_delta = _delta(
        latest.get("favorites") if latest else None,
        previous.get("favorites") if previous else None,
    )
    rating_count_delta = _delta(
        latest.get("rating_count") if latest else None,
        previous.get("rating_count") if previous else None,
    )
    return {
        **row,
        "analysis_version": ANALYSIS_VERSION,
        "sampled_this_run": bool(latest and int(latest["run_id"]) == analysis_run_id),
        "latest_metric_run_id": int(latest["run_id"]) if latest else None,
        "previous_metric_run_id": int(previous["run_id"]) if previous else None,
        "previous_metric_observed_utc": _utc_text(previous_utc),
        "elapsed_since_previous_hours": (
            round(elapsed_hours, 4) if elapsed_hours is not None else None
        ),
        "previous_followers": previous.get("followers") if previous else None,
        "previous_total_views": previous.get("total_views") if previous else None,
        "previous_chapter_count": previous.get("chapter_count") if previous else None,
        "previous_favorites": previous.get("favorites") if previous else None,
        "previous_rating_count": previous.get("rating_count") if previous else None,
        "follower_delta_since_previous": follower_delta,
        "view_delta_since_previous": view_delta,
        "chapter_delta_since_previous": chapter_delta,
        "favorite_delta_since_previous": favorite_delta,
        "rating_count_delta_since_previous": rating_count_delta,
        "follower_pct_change_since_previous": _pct_change(
            latest.get("followers") if latest else None,
            previous.get("followers") if previous else None,
        ),
        "view_pct_change_since_previous": _pct_change(
            latest.get("total_views") if latest else None,
            previous.get("total_views") if previous else None,
        ),
        "followers_per_day_increment": _rate_per_day(follower_delta, elapsed_hours),
        "views_per_day_increment": _rate_per_day(view_delta, elapsed_hours),
    }


def _persist_analysis_rows(
    storage: Storage,
    run_id: int,
    observed_utc: datetime,
    rows: Sequence[dict[str, Any]],
) -> None:
    ensure_longitudinal_schema(storage)
    with storage.connect() as conn:
        for row in rows:
            conn.execute(
                """
                INSERT OR REPLACE INTO analysis_observation(
                  run_id,fiction_id,analysis_version,observed_utc,
                  latest_metric_run_id,latest_metric_observed_utc,
                  previous_metric_run_id,previous_metric_observed_utc,elapsed_hours,
                  followers,total_views,chapter_count,favorites,rating_count,
                  follower_delta,view_delta,chapter_delta,favorite_delta,rating_count_delta,
                  follower_pct_change,view_pct_change,
                  followers_per_day_increment,views_per_day_increment,
                  current_rs,current_best_rs_rank,launch_index,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    row["fiction_id"],
                    ANALYSIS_VERSION,
                    _utc_text(observed_utc),
                    row.get("latest_metric_run_id"),
                    row.get("latest_metric_observed_utc"),
                    row.get("previous_metric_run_id"),
                    row.get("previous_metric_observed_utc"),
                    row.get("elapsed_since_previous_hours"),
                    row.get("followers"),
                    row.get("total_views"),
                    row.get("chapter_count"),
                    row.get("favorites"),
                    row.get("rating_count"),
                    row.get("follower_delta_since_previous"),
                    row.get("view_delta_since_previous"),
                    row.get("chapter_delta_since_previous"),
                    row.get("favorite_delta_since_previous"),
                    row.get("rating_count_delta_since_previous"),
                    row.get("follower_pct_change_since_previous"),
                    row.get("view_pct_change_since_previous"),
                    row.get("followers_per_day_increment"),
                    row.get("views_per_day_increment"),
                    int(bool(row.get("current_rs"))),
                    row.get("current_best_rs_rank"),
                    row.get("launch_index"),
                    json.dumps(row, ensure_ascii=False, sort_keys=True),
                ),
            )
        conn.commit()


def build_longitudinal_analysis(
    storage: Storage,
    run_id: int,
    fiction_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    ensure_longitudinal_schema(storage)
    with storage.connect() as conn:
        run = conn.execute(
            "SELECT timestamp_utc FROM run WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if run is None:
            raise ValueError(f"unknown run {run_id}")
        observed_utc = _parse_utc(str(run["timestamp_utc"]))
        assert observed_utc is not None
        if fiction_ids is None:
            tracked = conn.execute(
                """
                SELECT fiction_id FROM longitudinal_tracking
                WHERE active=1 ORDER BY tracking_started_utc,fiction_id
                """
            ).fetchall()
            ordered_ids = [str(row["fiction_id"]) for row in tracked]
        else:
            ordered_ids = list(dict.fromkeys(str(value) for value in fiction_ids))

    launch = build_launch_analysis(
        storage.db_path,
        run_id,
        current_launch_ids=ordered_ids,
        lookback_hours=24 * 365 * 20,
    )
    base_rows = launch["current_batch"]["novels"]
    with storage.connect() as conn:
        rows = [
            _augment_with_previous(
                row,
                _metric_history(conn, row["fiction_id"], run_id),
                run_id,
            )
            for row in base_rows
        ]

    _persist_analysis_rows(storage, run_id, observed_utc, rows)
    view_deltas = [
        float(row["view_delta_since_previous"])
        for row in rows
        if row.get("view_delta_since_previous") is not None
    ]
    follower_deltas = [
        float(row["follower_delta_since_previous"])
        for row in rows
        if row.get("follower_delta_since_previous") is not None
    ]
    sampled = sum(bool(row.get("sampled_this_run")) for row in rows)
    positive_views = sum(
        (row.get("view_delta_since_previous") or 0) > 0 for row in rows
    )
    positive_followers = sum(
        (row.get("follower_delta_since_previous") or 0) > 0 for row in rows
    )
    rows.sort(
        key=lambda row: (
            row.get("view_delta_since_previous") is not None,
            row.get("view_delta_since_previous") or -1,
            row.get("follower_delta_since_previous") or -1,
        ),
        reverse=True,
    )
    return {
        "schema_version": 1,
        "analysis_version": ANALYSIS_VERSION,
        "generated_utc": _utc_text(datetime.now(timezone.utc)),
        "run_id": run_id,
        "run_timestamp_utc": _utc_text(observed_utc),
        "methodology": {
            "raw_storage": "append_only_by_run_id",
            "comparison": "latest_detail_observation_vs_immediately_previous_detail_observation",
            "analysis_persistence": "one versioned row per run and fiction",
            "views_role": "outcome_only",
        },
        "summary": {
            "tracked_fictions": len(rows),
            "sampled_this_run": sampled,
            "not_sampled_this_run": len(rows) - sampled,
            "with_previous_observation": sum(
                row.get("previous_metric_run_id") is not None for row in rows
            ),
            "positive_view_delta": positive_views,
            "positive_follower_delta": positive_followers,
            "median_view_delta": round(median(view_deltas), 4) if view_deltas else None,
            "median_follower_delta": (
                round(median(follower_deltas), 4) if follower_deltas else None
            ),
        },
        "novels": rows,
    }


def _write_rows_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    fields = [
        "analysis_version",
        "fiction_id",
        "title",
        "author",
        "url",
        "sampled_this_run",
        "latest_metric_run_id",
        "latest_metric_observed_utc",
        "previous_metric_run_id",
        "previous_metric_observed_utc",
        "elapsed_since_previous_hours",
        "followers",
        "previous_followers",
        "follower_delta_since_previous",
        "follower_pct_change_since_previous",
        "followers_per_day_increment",
        "total_views",
        "previous_total_views",
        "view_delta_since_previous",
        "view_pct_change_since_previous",
        "views_per_day_increment",
        "chapter_count",
        "previous_chapter_count",
        "chapter_delta_since_previous",
        "favorites",
        "favorite_delta_since_previous",
        "rating_count",
        "rating_count_delta_since_previous",
        "launch_index",
        "launch_index_confidence",
        "current_rs",
        "current_best_rs_rank",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_history_csv(storage: Storage, path: Path) -> None:
    with storage.connect() as conn, path.open("w", encoding="utf-8", newline="") as handle:
        rows = conn.execute(
            """
            SELECT ao.*,f.title,f.author,f.url
            FROM analysis_observation ao
            JOIN fiction f USING(fiction_id)
            ORDER BY ao.fiction_id,ao.observed_utc,ao.analysis_version
            """
        ).fetchall()
        fields = [
            "run_id",
            "fiction_id",
            "title",
            "author",
            "url",
            "analysis_version",
            "observed_utc",
            "latest_metric_run_id",
            "latest_metric_observed_utc",
            "previous_metric_run_id",
            "previous_metric_observed_utc",
            "elapsed_hours",
            "followers",
            "total_views",
            "chapter_count",
            "favorites",
            "rating_count",
            "follower_delta",
            "view_delta",
            "chapter_delta",
            "favorite_delta",
            "rating_count_delta",
            "follower_pct_change",
            "view_pct_change",
            "followers_per_day_increment",
            "views_per_day_increment",
            "current_rs",
            "current_best_rs_rank",
            "launch_index",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(dict(row) for row in rows)


def _render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Royal Road longitudinal panel",
        "",
        f"- Run: `{report['run_id']}` at `{report['run_timestamp_utc']}`",
        f"- Analysis version: `{report['analysis_version']}`",
        f"- Tracked fiction: **{summary['tracked_fictions']}**",
        f"- Sampled this run: **{summary['sampled_this_run']}**",
        f"- With a previous observation: **{summary['with_previous_observation']}**",
        f"- Positive view delta: **{summary['positive_view_delta']}**",
        f"- Positive follower delta: **{summary['positive_follower_delta']}**",
        "",
        "## Largest changes since the previous detail observation",
        "",
        "| Novel | Δ views | Δ followers | Δ chapters | Elapsed h | Views/day | Followers/day |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["novels"][:30]:
        title = str(row.get("title") or row["fiction_id"]).replace("|", "\\|")
        lines.append(
            "| [{title}]({url}) | {views} | {followers} | {chapters} | {elapsed} | {vpd} | {fpd} |".format(
                title=title,
                url=row.get("url") or "",
                views=row.get("view_delta_since_previous") if row.get("view_delta_since_previous") is not None else "—",
                followers=row.get("follower_delta_since_previous") if row.get("follower_delta_since_previous") is not None else "—",
                chapters=row.get("chapter_delta_since_previous") if row.get("chapter_delta_since_previous") is not None else "—",
                elapsed=row.get("elapsed_since_previous_hours") if row.get("elapsed_since_previous_hours") is not None else "—",
                vpd=row.get("views_per_day_increment") if row.get("views_per_day_increment") is not None else "—",
                fpd=row.get("followers_per_day_increment") if row.get("followers_per_day_increment") is not None else "—",
            )
        )
    lines += [
        "",
        "> Raw metrics are never overwritten. Every successful detail request creates a new run-scoped observation; every analysis run creates a versioned derived row.",
        "",
    ]
    return "\n".join(lines)


def write_longitudinal_analysis(
    storage: Storage,
    run_id: int,
    report_dir: Path,
    fiction_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    report = build_longitudinal_analysis(storage, run_id, fiction_ids)
    report_dir.mkdir(parents=True, exist_ok=True)
    json_text = json.dumps(report, indent=2, ensure_ascii=False)
    markdown_text = _render_markdown(report)
    run_json = report_dir / f"longitudinal_analysis_run_{run_id}.json"
    latest_json = report_dir / "longitudinal_analysis_latest.json"
    run_md = report_dir / f"longitudinal_analysis_run_{run_id}.md"
    latest_md = report_dir / "longitudinal_analysis_latest.md"
    run_csv = report_dir / f"longitudinal_analysis_run_{run_id}.csv"
    latest_csv = report_dir / "longitudinal_analysis_latest.csv"
    history_csv = report_dir / "longitudinal_history_latest.csv"
    run_json.write_text(json_text + "\n", encoding="utf-8")
    latest_json.write_text(json_text + "\n", encoding="utf-8")
    run_md.write_text(markdown_text, encoding="utf-8")
    latest_md.write_text(markdown_text, encoding="utf-8")
    _write_rows_csv(run_csv, report["novels"])
    _write_rows_csv(latest_csv, report["novels"])
    _write_history_csv(storage, history_csv)
    return {
        "report": report,
        "paths": {
            "json": str(run_json),
            "markdown": str(run_md),
            "csv": str(run_csv),
            "latest_json": str(latest_json),
            "latest_markdown": str(latest_md),
            "latest_csv": str(latest_csv),
            "history_csv": str(history_csv),
        },
    }


async def refresh_longitudinal_panel(
    settings: Settings | None = None,
    *,
    max_fictions: int | None = None,
    analysis_only: bool = False,
) -> dict[str, Any]:
    settings = settings or Settings()
    storage = Storage(settings.db_path, settings.raw_dir)
    ensure_longitudinal_schema(storage)
    newly_tracked = bootstrap_tracking(storage)
    timestamp = datetime.now(timezone.utc)
    run_id = storage.begin_run(timestamp, __version__)
    configured_max = _env_nonnegative_int("RR_LONGITUDINAL_MAX_PER_RUN", 0)
    resolved_max = configured_max if max_fictions is None else max_fictions
    candidates = tracked_candidates(
        storage,
        now=timestamp,
        max_fictions=resolved_max,
    )
    errors: list[str] = []
    success_count = 0
    unavailable_count = 0
    transient_failure_count = 0
    client = PublicHtmlClient(settings)
    try:
        if not analysis_only:
            for candidate in candidates:
                fiction_id = str(candidate["fiction_id"])
                try:
                    fetched = await client.get(str(candidate["url"]))
                    detail = parse_detail_html(
                        fetched.text,
                        str(candidate["url"]),
                        timestamp,
                    )
                    raw_path = storage.persist_detail(
                        run_id,
                        detail,
                        fetched.text if settings.save_raw_html else None,
                    )
                    record_detail_success(storage, fiction_id, timestamp)
                    _record_attempt(
                        storage,
                        run_id=run_id,
                        fiction_id=fiction_id,
                        attempted_utc=timestamp,
                        outcome="success",
                        availability="available",
                        http_status=fetched.status_code,
                        raw_json_path=str(raw_path),
                    )
                    success_count += 1
                except Exception as exc:
                    state = record_detail_failure(storage, fiction_id, timestamp, exc)
                    _record_attempt(
                        storage,
                        run_id=run_id,
                        fiction_id=fiction_id,
                        attempted_utc=timestamp,
                        outcome="failure",
                        availability=str(state["availability"]),
                        http_status=state.get("http_status"),
                        error=exc,
                    )
                    if state["availability"] == "unavailable":
                        unavailable_count += 1
                    else:
                        transient_failure_count += 1
                        errors.append(
                            f"detail {fiction_id}: {type(exc).__name__}: {exc}"
                        )
        derive_run(settings.db_path, run_id)
        output = write_longitudinal_analysis(
            storage,
            run_id,
            settings.report_dir,
        )
        status = "complete" if not errors else "partial"
        storage.finish_run(run_id, status, "\n".join(errors) or None)
        return {
            "run_id": run_id,
            "timestamp_utc": _utc_text(timestamp),
            "status": status,
            "analysis_version": ANALYSIS_VERSION,
            "newly_tracked": newly_tracked,
            "tracked_candidates": len(candidates),
            "details_successful": success_count,
            "details_unavailable": unavailable_count,
            "details_transient_failure": transient_failure_count,
            "analysis_summary": output["report"]["summary"],
            "analysis_paths": output["paths"],
            "errors": errors,
        }
    except Exception as exc:
        storage.finish_run(run_id, "failed", f"{type(exc).__name__}: {exc}")
        raise
    finally:
        await client.close()


def run_longitudinal_refresh(
    settings: Settings | None = None,
    *,
    max_fictions: int | None = None,
    analysis_only: bool = False,
) -> dict[str, Any]:
    return asyncio.run(
        refresh_longitudinal_panel(
            settings,
            max_fictions=max_fictions,
            analysis_only=analysis_only,
        )
    )
