from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

HORIZONS = (1, 6, 12, 24, 72, 168)


def _canonical_metrics(conn: sqlite3.Connection, run_id: int) -> dict[str, sqlite3.Row]:
    rows = conn.execute(
        """
        WITH ranked AS (
          SELECT mo.*,
                 ROW_NUMBER() OVER (
                   PARTITION BY mo.fiction_id
                   ORDER BY CASE WHEN source_name='fiction_detail' THEN 0
                                 WHEN source_name LIKE 'rs_%' THEN 1 ELSE 2 END,
                            (followers IS NOT NULL) DESC,
                            (total_views IS NOT NULL) DESC
                 ) AS rn
          FROM metric_observation mo WHERE run_id=?
        )
        SELECT * FROM ranked WHERE rn=1
        """,
        (run_id,),
    ).fetchall()
    return {row["fiction_id"]: row for row in rows}


def derive_run(db_path: Path, run_id: int) -> None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        current_run = conn.execute("SELECT timestamp_utc FROM run WHERE run_id=?", (run_id,)).fetchone()
        if not current_run:
            raise ValueError(f"unknown run {run_id}")
        current_ts = datetime.fromisoformat(current_run["timestamp_utc"].replace("Z", "+00:00"))

        # Transitions and dynamic rank-50 cutoffs for Rising Stars sources.
        sources = [row[0] for row in conn.execute(
            "SELECT source_name FROM source_snapshot WHERE run_id=? AND source_family='rising_stars'",
            (run_id,),
        )]
        for source in sources:
            prior = conn.execute(
                """
                SELECT MAX(lm.run_id) FROM listing_membership lm
                JOIN run r ON r.run_id=lm.run_id
                WHERE lm.source_name=? AND lm.run_id<?
                """,
                (source, run_id),
            ).fetchone()[0]
            current_map = {row["fiction_id"]: row["rank"] for row in conn.execute(
                "SELECT fiction_id,rank FROM listing_membership WHERE run_id=? AND source_name=?",
                (run_id, source),
            )}
            prior_map = {} if prior is None else {row["fiction_id"]: row["rank"] for row in conn.execute(
                "SELECT fiction_id,rank FROM listing_membership WHERE run_id=? AND source_name=?",
                (prior, source),
            )}
            all_ids = set(current_map) | set(prior_map)
            for fiction_id in all_ids:
                old = prior_map.get(fiction_id)
                new = current_map.get(fiction_id)
                if old is None:
                    kind = "entry"
                elif new is None:
                    kind = "exit"
                elif new < old:
                    kind = "rise"
                elif new > old:
                    kind = "fall"
                else:
                    kind = "flat"
                conn.execute(
                    "INSERT OR REPLACE INTO list_transition(run_id,source_name,fiction_id,transition_type,prior_rank,current_rank,rank_delta) VALUES(?,?,?,?,?,?,?)",
                    (run_id, source, fiction_id, kind, old, new, (old - new) if old is not None and new is not None else None),
                )
            cutoff = conn.execute(
                """
                SELECT lm.fiction_id,mo.followers,mo.total_views,mo.page_count,mo.chapter_count
                FROM listing_membership lm
                LEFT JOIN metric_observation mo ON mo.run_id=lm.run_id AND mo.fiction_id=lm.fiction_id AND mo.source_name=lm.source_name
                WHERE lm.run_id=? AND lm.source_name=? AND lm.rank=50
                """,
                (run_id, source),
            ).fetchone()
            conn.execute(
                "INSERT OR REPLACE INTO list_cutoff(run_id,source_name,cutoff_rank,fiction_id,followers,total_views,page_count,chapter_count) VALUES(?,?,?,?,?,?,?,?)",
                (run_id, source, 50, cutoff["fiction_id"] if cutoff else None, cutoff["followers"] if cutoff else None,
                 cutoff["total_views"] if cutoff else None, cutoff["page_count"] if cutoff else None,
                 cutoff["chapter_count"] if cutoff else None),
            )

        current = _canonical_metrics(conn, run_id)
        for horizon in HORIZONS:
            target_ts = current_ts.timestamp() - horizon * 3600
            prior_row = conn.execute(
                """
                SELECT run_id,timestamp_utc,ABS(strftime('%s',timestamp_utc)-?) AS distance
                FROM run WHERE run_id<? AND status IN ('complete','partial')
                ORDER BY distance ASC LIMIT 1
                """,
                (target_ts, run_id),
            ).fetchone()
            if not prior_row:
                continue
            prior_ts = datetime.fromisoformat(prior_row["timestamp_utc"].replace("Z", "+00:00"))
            elapsed = (current_ts - prior_ts).total_seconds() / 3600
            # Do not label a wildly distant observation as a precise horizon.
            tolerance = max(1.5, horizon * 0.35)
            if abs(elapsed - horizon) > tolerance:
                continue
            prior_metrics = _canonical_metrics(conn, prior_row["run_id"])
            for fiction_id, now in current.items():
                before = prior_metrics.get(fiction_id)
                if not before:
                    continue
                def delta(field: str):
                    return None if now[field] is None or before[field] is None else now[field] - before[field]
                conn.execute(
                    """
                    INSERT OR REPLACE INTO metric_delta(
                      run_id,fiction_id,horizon_hours,prior_run_id,elapsed_hours,follower_delta,view_delta,
                      favorite_delta,rating_count_delta,chapter_delta,page_delta
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (run_id, fiction_id, horizon, prior_row["run_id"], elapsed,
                     delta("followers"), delta("total_views"), delta("favorites"),
                     delta("rating_count"), delta("chapter_count"), delta("page_count")),
                )
        conn.commit()
    finally:
        conn.close()
