from __future__ import annotations

import sqlite3
from pathlib import Path


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def latest_source(db_path: Path, source_name: str) -> list[dict]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            WITH latest AS (
              SELECT MAX(run_id) run_id FROM listing_membership WHERE source_name=?
            )
            SELECT r.timestamp_utc,lm.source_name,lm.rank,lm.fiction_id,f.title,f.author,
                   mo.followers,mo.total_views,mo.page_count,mo.chapter_count,mo.rating_count,
                   mo.rating_average,mo.first_chapter_utc,mo.last_update_utc
            FROM listing_membership lm JOIN latest l ON l.run_id=lm.run_id
            JOIN run r USING(run_id) JOIN fiction f USING(fiction_id)
            LEFT JOIN metric_observation mo ON mo.run_id=lm.run_id AND mo.source_name=lm.source_name AND mo.fiction_id=lm.fiction_id
            WHERE lm.source_name=? ORDER BY lm.rank
            """,
            (source_name, source_name),
        ).fetchall()
        return [dict(row) for row in rows]


def fiction_history(db_path: Path, fiction_id: str, limit: int = 500) -> list[dict]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT r.run_id,r.timestamp_utc,mo.source_name,mo.followers,mo.total_views,
                   mo.average_views,mo.favorites,mo.page_count,mo.chapter_count,
                   mo.word_count,mo.word_count_estimate,mo.rating_count,mo.rating_average,
                   mo.review_count,mo.comment_count
            FROM metric_observation mo JOIN run r USING(run_id)
            WHERE mo.fiction_id=? ORDER BY r.timestamp_utc DESC LIMIT ?
            """,
            (fiction_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]


def longitudinal_history(
    db_path: Path,
    fiction_id: str,
    limit: int = 500,
    analysis_version: str | None = None,
) -> list[dict]:
    with _connect(db_path) as conn:
        exists = conn.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type='table' AND name='analysis_observation'
            """
        ).fetchone()
        if exists is None:
            return []
        version_clause = "" if analysis_version is None else "AND ao.analysis_version=?"
        parameters: tuple[object, ...]
        if analysis_version is None:
            parameters = (str(fiction_id), limit)
        else:
            parameters = (str(fiction_id), analysis_version, limit)
        rows = conn.execute(
            f"""
            SELECT ao.run_id,ao.observed_utc,ao.analysis_version,
                   ao.latest_metric_run_id,ao.latest_metric_observed_utc,
                   ao.previous_metric_run_id,ao.previous_metric_observed_utc,
                   ao.elapsed_hours,ao.followers,ao.total_views,ao.chapter_count,
                   ao.favorites,ao.rating_count,ao.follower_delta,ao.view_delta,
                   ao.chapter_delta,ao.favorite_delta,ao.rating_count_delta,
                   ao.follower_pct_change,ao.view_pct_change,
                   ao.followers_per_day_increment,ao.views_per_day_increment,
                   ao.current_rs,ao.current_best_rs_rank,ao.launch_index
            FROM analysis_observation ao
            WHERE ao.fiction_id=? {version_clause}
            ORDER BY ao.observed_utc DESC,ao.analysis_version
            LIMIT ?
            """,
            parameters,
        ).fetchall()
        return [dict(row) for row in rows]


def new_entrants(db_path: Path, source_name: str) -> list[dict]:
    with _connect(db_path) as conn:
        latest_ids = [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT run_id FROM listing_membership WHERE source_name=? ORDER BY run_id DESC LIMIT 2",
                (source_name,),
            )
        ]
        if len(latest_ids) < 2:
            return []
        latest, previous = latest_ids
        rows = conn.execute(
            """
            SELECT lm.rank,lm.fiction_id,f.title,f.author,r.timestamp_utc
            FROM listing_membership lm JOIN fiction f USING(fiction_id) JOIN run r USING(run_id)
            WHERE lm.run_id=? AND lm.source_name=? AND lm.fiction_id NOT IN (
              SELECT fiction_id FROM listing_membership WHERE run_id=? AND source_name=?
            ) ORDER BY lm.rank
            """,
            (latest, source_name, previous, source_name),
        ).fetchall()
        return [dict(row) for row in rows]


def diagnostics_seed(db_path: Path, run_id: int | None = None) -> list[dict]:
    with _connect(db_path) as conn:
        if run_id is None:
            run_id = conn.execute(
                "SELECT MAX(run_id) FROM run WHERE status IN ('complete','partial')"
            ).fetchone()[0]
        rows = conn.execute(
            """
            WITH overlap AS (
              SELECT fiction_id,COUNT(*) rs_list_count,MIN(rank) best_rs_rank
              FROM listing_membership lm JOIN source_snapshot ss USING(run_id,source_name)
              WHERE lm.run_id=? AND ss.source_family='rising_stars' GROUP BY fiction_id
            ), latest_delta AS (
              SELECT * FROM metric_delta WHERE run_id=? AND horizon_hours=24
            )
            SELECT f.fiction_id,f.title,f.author,o.rs_list_count,o.best_rs_rank,
                   d.follower_delta,d.view_delta,d.chapter_delta,d.elapsed_hours
            FROM overlap o JOIN fiction f USING(fiction_id)
            LEFT JOIN latest_delta d USING(fiction_id)
            ORDER BY o.best_rs_rank,o.rs_list_count DESC
            """,
            (run_id, run_id),
        ).fetchall()
        return [dict(row) for row in rows]
