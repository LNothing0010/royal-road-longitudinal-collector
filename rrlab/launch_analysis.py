from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Sequence

CORE_METRICS = ("followers", "total_views", "chapter_count")
SCORE_COMPONENTS = (
    ("followers_per_day", 0.25),
    ("views_per_day", 0.20),
    ("followers_per_1000_views", 0.20),
    ("followers_per_chapter", 0.15),
    ("views_per_chapter", 0.10),
    ("follower_growth_per_day", 0.10),
)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_text(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def _round(value: float | int | None, digits: int = 3) -> float | int | None:
    if value is None:
        return None
    return round(float(value), digits)


def _safe_ratio(
    numerator: int | float | None,
    denominator: int | float | None,
    scale: float = 1.0,
) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return float(numerator) / float(denominator) * scale


def _percentile(values: Sequence[float], value: float) -> float:
    if not values:
        return 0.0
    less = sum(candidate < value for candidate in values)
    equal = sum(candidate == value for candidate in values)
    return 100.0 * (less + 0.5 * equal) / len(values)


def _placeholders(values: Sequence[str]) -> str:
    return ",".join("?" for _ in values)


def _metric_series(
    conn: sqlite3.Connection,
    run_id: int,
    fiction_ids: Sequence[str],
) -> dict[str, list[dict[str, Any]]]:
    if not fiction_ids:
        return {}
    rows = conn.execute(
        f"""
        SELECT r.run_id,r.timestamp_utc,mo.*
        FROM metric_observation AS mo
        JOIN run AS r USING(run_id)
        WHERE mo.run_id<=?
          AND mo.source_name='fiction_detail'
          AND mo.fiction_id IN ({_placeholders(fiction_ids)})
        ORDER BY mo.fiction_id,r.timestamp_utc,mo.run_id
        """,
        (run_id, *fiction_ids),
    ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {
        fiction_id: [] for fiction_id in fiction_ids
    }
    for row in rows:
        grouped.setdefault(str(row["fiction_id"]), []).append(dict(row))
    return grouped


def _fiction_rows(
    conn: sqlite3.Connection,
    fiction_ids: Sequence[str],
) -> dict[str, dict[str, Any]]:
    if not fiction_ids:
        return {}
    rows = conn.execute(
        f"""
        SELECT fiction_id,title,author,url,first_seen_utc,last_seen_utc,first_seen_source
        FROM fiction
        WHERE fiction_id IN ({_placeholders(fiction_ids)})
        """,
        tuple(fiction_ids),
    ).fetchall()
    return {str(row["fiction_id"]): dict(row) for row in rows}


def _first_release_dates(
    conn: sqlite3.Connection,
    fiction_ids: Sequence[str],
) -> dict[str, str]:
    if not fiction_ids:
        return {}
    rows = conn.execute(
        f"""
        SELECT fiction_id,MIN(published_utc) AS first_published_utc
        FROM release_event
        WHERE fiction_id IN ({_placeholders(fiction_ids)})
          AND published_utc IS NOT NULL
        GROUP BY fiction_id
        """,
        tuple(fiction_ids),
    ).fetchall()
    return {
        str(row["fiction_id"]): str(row["first_published_utc"])
        for row in rows
    }


def _first_newest_ranks(
    conn: sqlite3.Connection,
    fiction_ids: Sequence[str],
) -> dict[str, int]:
    if not fiction_ids:
        return {}
    rows = conn.execute(
        f"""
        WITH ranked AS (
          SELECT lm.fiction_id,lm.rank,
                 ROW_NUMBER() OVER(
                   PARTITION BY lm.fiction_id ORDER BY lm.run_id
                 ) AS rn
          FROM listing_membership AS lm
          WHERE lm.source_name='newest'
            AND lm.fiction_id IN ({_placeholders(fiction_ids)})
        )
        SELECT fiction_id,rank FROM ranked WHERE rn=1
        """,
        tuple(fiction_ids),
    ).fetchall()
    return {
        str(row["fiction_id"]): int(row["rank"])
        for row in rows
        if row["rank"] is not None
    }


def _rs_context(
    conn: sqlite3.Connection,
    run_id: int,
    fiction_ids: Sequence[str],
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    if not fiction_ids:
        return {}, {}
    latest_rs_run = conn.execute(
        """
        SELECT MAX(run_id)
        FROM source_snapshot
        WHERE source_family='rising_stars' AND run_id<=?
        """,
        (run_id,),
    ).fetchone()[0]
    current: dict[str, dict[str, Any]] = {}
    if latest_rs_run is not None:
        rows = conn.execute(
            f"""
            SELECT lm.fiction_id,MIN(lm.rank) AS best_rank,
                   COUNT(DISTINCT lm.source_name) AS list_count,
                   GROUP_CONCAT(DISTINCT lm.source_name) AS sources
            FROM listing_membership AS lm
            JOIN source_snapshot AS ss USING(run_id,source_name)
            WHERE lm.run_id=?
              AND ss.source_family='rising_stars'
              AND lm.fiction_id IN ({_placeholders(fiction_ids)})
            GROUP BY lm.fiction_id
            """,
            (latest_rs_run, *fiction_ids),
        ).fetchall()
        current = {
            str(row["fiction_id"]): {
                "best_rank": int(row["best_rank"]),
                "list_count": int(row["list_count"]),
                "sources": sorted(str(row["sources"] or "").split(",")),
            }
            for row in rows
        }

    rows = conn.execute(
        f"""
        SELECT lm.fiction_id,MIN(r.timestamp_utc) AS first_rs_utc
        FROM listing_membership AS lm
        JOIN source_snapshot AS ss USING(run_id,source_name)
        JOIN run AS r USING(run_id)
        WHERE ss.source_family='rising_stars'
          AND lm.run_id<=?
          AND lm.fiction_id IN ({_placeholders(fiction_ids)})
        GROUP BY lm.fiction_id
        """,
        (run_id, *fiction_ids),
    ).fetchall()
    first = {
        str(row["fiction_id"]): str(row["first_rs_utc"])
        for row in rows
    }
    return current, first


def _availability_rows(
    conn: sqlite3.Connection,
    fiction_ids: Sequence[str],
) -> dict[str, dict[str, Any]]:
    table = conn.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type='table' AND name='detail_fetch_state'"
    ).fetchone()
    if table is None or not fiction_ids:
        return {}
    rows = conn.execute(
        f"""
        SELECT * FROM detail_fetch_state
        WHERE fiction_id IN ({_placeholders(fiction_ids)})
        """,
        tuple(fiction_ids),
    ).fetchall()
    return {str(row["fiction_id"]): dict(row) for row in rows}


def _cohort_ids(
    conn: sqlite3.Connection,
    run_id: int,
    lookback_hours: int,
) -> tuple[datetime, list[str]]:
    run = conn.execute(
        "SELECT timestamp_utc FROM run WHERE run_id=?",
        (run_id,),
    ).fetchone()
    if run is None:
        raise ValueError(f"unknown run {run_id}")
    run_utc = _parse_utc(str(run["timestamp_utc"]))
    assert run_utc is not None
    cutoff = run_utc - timedelta(hours=lookback_hours)
    rows = conn.execute(
        """
        SELECT fiction.fiction_id
        FROM fiction AS fiction
        WHERE EXISTS (
            SELECT 1 FROM listing_membership AS newest_membership
            WHERE newest_membership.fiction_id=fiction.fiction_id
              AND newest_membership.source_name='newest'
          )
          AND julianday(first_seen_utc)>=julianday(?)
          AND julianday(first_seen_utc)<=julianday(?)
        ORDER BY first_seen_utc DESC,fiction_id DESC
        """,
        (_utc_text(cutoff), _utc_text(run_utc)),
    ).fetchall()
    return run_utc, [str(row["fiction_id"]) for row in rows]


def _build_rows(
    conn: sqlite3.Connection,
    run_id: int,
    run_utc: datetime,
    fiction_ids: Sequence[str],
) -> list[dict[str, Any]]:
    ordered_ids = list(dict.fromkeys(str(value) for value in fiction_ids))
    fictions = _fiction_rows(conn, ordered_ids)
    series = _metric_series(conn, run_id, ordered_ids)
    releases = _first_release_dates(conn, ordered_ids)
    newest_ranks = _first_newest_ranks(conn, ordered_ids)
    current_rs, first_rs = _rs_context(conn, run_id, ordered_ids)
    availability = _availability_rows(conn, ordered_ids)

    results: list[dict[str, Any]] = []
    for fiction_id in ordered_ids:
        fiction = fictions.get(fiction_id)
        if fiction is None:
            continue
        observations = series.get(fiction_id, [])
        baseline = observations[0] if observations else None
        latest = observations[-1] if observations else None
        state = availability.get(fiction_id, {})

        first_seen = _parse_utc(str(fiction["first_seen_utc"]))
        published = _parse_utc(releases.get(fiction_id))
        if published is None and baseline:
            published = _parse_utc(baseline.get("first_chapter_utc"))
        age_basis = "first_chapter" if published is not None else "first_seen_proxy"
        age_start = published or first_seen or run_utc
        age_hours = max(
            0.0,
            (run_utc - age_start).total_seconds() / 3600.0,
        )
        effective_age_hours = max(6.0, age_hours)

        followers = latest.get("followers") if latest else None
        total_views = latest.get("total_views") if latest else None
        chapters = latest.get("chapter_count") if latest else None
        pages = latest.get("page_count") if latest else None
        baseline_utc = (
            _parse_utc(baseline.get("timestamp_utc")) if baseline else None
        )
        latest_utc = (
            _parse_utc(latest.get("timestamp_utc")) if latest else None
        )
        span_hours = (
            max(
                0.0,
                (latest_utc - baseline_utc).total_seconds() / 3600.0,
            )
            if latest_utc and baseline_utc
            else 0.0
        )
        follower_growth = (
            followers - baseline["followers"]
            if latest
            and baseline
            and followers is not None
            and baseline["followers"] is not None
            else None
        )
        view_growth = (
            total_views - baseline["total_views"]
            if latest
            and baseline
            and total_views is not None
            and baseline["total_views"] is not None
            else None
        )
        chapter_growth = (
            chapters - baseline["chapter_count"]
            if latest
            and baseline
            and chapters is not None
            and baseline["chapter_count"] is not None
            else None
        )
        first_rs_utc = _parse_utc(first_rs.get(fiction_id))
        hours_to_rs = (
            max(
                0.0,
                (first_rs_utc - age_start).total_seconds() / 3600.0,
            )
            if first_rs_utc
            else None
        )

        core_complete = latest is not None and all(
            latest.get(field) is not None for field in CORE_METRICS
        )
        if core_complete:
            data_status = "complete"
        elif state.get("availability") == "unavailable":
            data_status = "unavailable"
        elif latest is not None:
            data_status = "partial"
        else:
            data_status = "missing"

        rs = current_rs.get(fiction_id, {})
        result = {
            "fiction_id": fiction_id,
            "title": fiction["title"],
            "author": fiction["author"],
            "url": fiction["url"],
            "discovery_source": fiction["first_seen_source"],
            "first_seen_utc": fiction["first_seen_utc"],
            "newest_rank_at_discovery": newest_ranks.get(fiction_id),
            "published_utc": _utc_text(published),
            "age_basis": age_basis,
            "age_hours": _round(age_hours, 2),
            "baseline_observed_utc": _utc_text(baseline_utc),
            "latest_metric_observed_utc": _utc_text(latest_utc),
            "detail_observation_count": len(observations),
            "observation_span_hours": _round(span_hours, 2),
            "data_status": data_status,
            "availability": state.get(
                "availability",
                "unknown" if latest is None else "available",
            ),
            "http_status": state.get("http_status"),
            "next_detail_retry_utc": state.get("next_retry_utc"),
            "followers": followers,
            "total_views": total_views,
            "average_views": latest.get("average_views") if latest else None,
            "favorites": latest.get("favorites") if latest else None,
            "page_count": pages,
            "chapter_count": chapters,
            "word_count": latest.get("word_count") if latest else None,
            "word_count_estimate": (
                latest.get("word_count_estimate") if latest else None
            ),
            "rating_count": latest.get("rating_count") if latest else None,
            "rating_average": latest.get("rating_average") if latest else None,
            "review_count": latest.get("review_count") if latest else None,
            "comment_count": latest.get("comment_count") if latest else None,
            "follower_growth": follower_growth,
            "view_growth": view_growth,
            "chapter_growth": chapter_growth,
            "followers_per_day": _round(
                _safe_ratio(followers, effective_age_hours, 24.0),
                2,
            ),
            "views_per_day": _round(
                _safe_ratio(total_views, effective_age_hours, 24.0),
                2,
            ),
            "followers_per_1000_views": _round(
                _safe_ratio(followers, total_views, 1000.0),
                2,
            ),
            "followers_per_chapter": _round(
                _safe_ratio(followers, chapters),
                2,
            ),
            "views_per_chapter": _round(
                _safe_ratio(total_views, chapters),
                2,
            ),
            "follower_growth_per_day": _round(
                _safe_ratio(follower_growth, span_hours, 24.0)
                if span_hours > 0
                else None,
                2,
            ),
            "view_growth_per_day": _round(
                _safe_ratio(view_growth, span_hours, 24.0)
                if span_hours > 0
                else None,
                2,
            ),
            "current_rs": fiction_id in current_rs,
            "current_best_rs_rank": rs.get("best_rank"),
            "current_rs_list_count": rs.get("list_count", 0),
            "current_rs_sources": rs.get("sources", []),
            "first_rs_utc": _utc_text(first_rs_utc),
            "hours_to_rs": _round(hours_to_rs, 2),
            "launch_index": None,
            "launch_index_confidence": "low",
            "launch_index_components": {},
        }
        results.append(result)

    _score_rows(results)
    return results


def _score_rows(rows: list[dict[str, Any]]) -> None:
    distributions = {
        field: [
            float(row[field])
            for row in rows
            if row.get(field) is not None
        ]
        for field, _ in SCORE_COMPONENTS
    }
    for row in rows:
        weighted = 0.0
        used_weight = 0.0
        components: dict[str, float] = {}
        for field, weight in SCORE_COMPONENTS:
            value = row.get(field)
            if value is None or not distributions[field]:
                continue
            score = _percentile(distributions[field], float(value))
            components[field] = round(score, 2)
            weighted += score * weight
            used_weight += weight
        row["launch_index_components"] = components
        if len(components) >= 4 and used_weight > 0:
            row["launch_index"] = round(weighted / used_weight, 2)
        if row["launch_index"] is not None:
            if (
                row["age_basis"] == "first_chapter"
                and row["observation_span_hours"] >= 6
            ):
                row["launch_index_confidence"] = "high"
            elif (
                row["age_basis"] == "first_chapter"
                or row["detail_observation_count"] >= 2
            ):
                row["launch_index_confidence"] = "medium"


def _median_value(
    rows: Sequence[dict[str, Any]],
    field: str,
) -> float | None:
    values = [
        float(row[field]) for row in rows if row.get(field) is not None
    ]
    return round(float(median(values)), 2) if values else None


def _leader(
    rows: Sequence[dict[str, Any]],
    field: str,
    *,
    lowest: bool = False,
) -> dict[str, Any] | None:
    candidates = [row for row in rows if row.get(field) is not None]
    if not candidates:
        return None
    winner = (
        min(candidates, key=lambda row: row[field])
        if lowest
        else max(candidates, key=lambda row: row[field])
    )
    return {
        "fiction_id": winner["fiction_id"],
        "title": winner["title"],
        "value": winner[field],
        "metric": field,
    }


def _summarize(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    complete = sum(row["data_status"] == "complete" for row in rows)
    unavailable = sum(
        row["data_status"] == "unavailable" for row in rows
    )
    partial = sum(row["data_status"] == "partial" for row in rows)
    missing = sum(row["data_status"] == "missing" for row in rows)
    return {
        "novel_count": total,
        "data_quality": {
            "core_metrics_complete": complete,
            "partial_metrics": partial,
            "unavailable_or_removed": unavailable,
            "missing_detail": missing,
            "core_coverage_pct": (
                round(100.0 * complete / total, 2) if total else 100.0
            ),
            "resolved_coverage_pct": (
                round(100.0 * (complete + unavailable) / total, 2)
                if total
                else 100.0
            ),
        },
        "medians": {
            field: _median_value(rows, field)
            for field in (
                "followers",
                "total_views",
                "chapter_count",
                "followers_per_day",
                "views_per_day",
                "followers_per_1000_views",
                "followers_per_chapter",
                "views_per_chapter",
                "launch_index",
            )
        },
        "leaders": {
            "overall_launch_index": _leader(rows, "launch_index"),
            "followers": _leader(rows, "followers"),
            "views": _leader(rows, "total_views"),
            "conversion": _leader(rows, "followers_per_1000_views"),
            "follower_momentum": _leader(
                rows,
                "follower_growth_per_day",
            ),
            "view_momentum": _leader(rows, "view_growth_per_day"),
            "best_current_rs_rank": _leader(
                rows,
                "current_best_rs_rank",
                lowest=True,
            ),
        },
        "exceptions": [
            {
                "fiction_id": row["fiction_id"],
                "title": row["title"],
                "data_status": row["data_status"],
                "availability": row["availability"],
                "http_status": row["http_status"],
                "next_detail_retry_utc": row["next_detail_retry_utc"],
            }
            for row in rows
            if row["data_status"] != "complete"
        ],
    }


def build_launch_analysis(
    db_path: Path,
    run_id: int | None = None,
    *,
    current_launch_ids: Sequence[str] | None = None,
    lookback_hours: int = 168,
) -> dict[str, Any]:
    with _connect(db_path) as conn:
        if run_id is None:
            row = conn.execute(
                """
                SELECT MAX(r.run_id)
                FROM run AS r
                WHERE EXISTS(
                  SELECT 1 FROM source_snapshot AS ss
                  WHERE ss.run_id=r.run_id
                    AND ss.source_family='rising_stars'
                )
                """
            ).fetchone()
            if row is None or row[0] is None:
                raise RuntimeError(
                    "No panel run is available for launch analysis"
                )
            run_id = int(row[0])

        run_utc, rolling_ids = _cohort_ids(
            conn,
            run_id,
            lookback_hours,
        )
        if current_launch_ids is None:
            rows = conn.execute(
                """
                SELECT fiction.fiction_id
                FROM fiction AS fiction
                WHERE EXISTS (
                  SELECT 1 FROM listing_membership AS newest_membership
                  WHERE newest_membership.fiction_id=fiction.fiction_id
                    AND newest_membership.source_name='newest'
                )
                  AND first_seen_utc=(
                    SELECT timestamp_utc FROM run WHERE run_id=?
                  )
                ORDER BY fiction_id DESC
                """,
                (run_id,),
            ).fetchall()
            current_ids = [str(row["fiction_id"]) for row in rows]
        else:
            current_ids = list(
                dict.fromkeys(str(value) for value in current_launch_ids)
            )

        all_ids = list(dict.fromkeys([*current_ids, *rolling_ids]))
        all_rows = _build_rows(conn, run_id, run_utc, all_ids)
        by_id = {row["fiction_id"]: row for row in all_rows}
        current_rows = [
            by_id[fiction_id]
            for fiction_id in current_ids
            if fiction_id in by_id
        ]
        rolling_rows = [
            by_id[fiction_id]
            for fiction_id in rolling_ids
            if fiction_id in by_id
        ]
        rolling_rows.sort(
            key=lambda row: (
                row["launch_index"] is not None,
                row["launch_index"] or -1,
                row["followers"] or -1,
            ),
            reverse=True,
        )

        return {
            "schema_version": 1,
            "generated_utc": _utc_text(datetime.now(timezone.utc)),
            "run_id": run_id,
            "run_timestamp_utc": _utc_text(run_utc),
            "methodology": {
                "current_batch": (
                    "Exact fiction IDs reported by the verified Newest "
                    "frontier for this run."
                ),
                "rolling_cohort_hours": lookback_hours,
                "core_metrics": list(CORE_METRICS),
                "age_normalization_floor_hours": 6,
                "launch_index": {
                    "description": (
                        "Weighted within-cohort percentile index; outcome "
                        "labels such as Rising Stars are not included."
                    ),
                    "components": {
                        field: weight
                        for field, weight in SCORE_COMPONENTS
                    },
                    "minimum_components": 4,
                },
            },
            "current_batch": {
                "summary": _summarize(current_rows),
                "novels": current_rows,
            },
            "rolling_cohort": {
                "lookback_hours": lookback_hours,
                "summary": _summarize(rolling_rows),
                "novels": rolling_rows,
            },
        }


def _display(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return f"{value:,}" if isinstance(value, int) else str(value)


def _escape(value: Any) -> str:
    return str(value or "—").replace("|", "\\|").replace("\n", " ")


def render_launch_markdown(report: dict[str, Any]) -> str:
    current = report["current_batch"]
    rolling = report["rolling_cohort"]
    current_quality = current["summary"]["data_quality"]
    rolling_quality = rolling["summary"]["data_quality"]
    lines = [
        "# Royal Road launch analysis",
        "",
        f"- Run: `{report['run_id']}` at `{report['run_timestamp_utc']}`",
        (
            "- Newly detected this run: "
            f"**{current['summary']['novel_count']}**"
        ),
        (
            "- Current-batch core metric coverage: "
            f"**{current_quality['core_coverage_pct']}%**"
        ),
        (
            f"- Rolling {rolling['lookback_hours']}h cohort: "
            f"**{rolling['summary']['novel_count']} novels**"
        ),
        (
            "- Rolling resolved coverage: "
            f"**{rolling_quality['resolved_coverage_pct']}%**"
        ),
        "",
        "## Newly published fiction captured this run",
        "",
    ]
    if not current["novels"]:
        lines.append(
            "No new fiction crossed the verified frontier in this run."
        )
    else:
        lines += [
            (
                "| Novel | Author | Status | Followers | Views | "
                "Chapters | F/1k views | Launch index |"
            ),
            "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
        for row in current["novels"]:
            title = f"[{_escape(row['title'])}]({row['url']})"
            lines.append(
                "| "
                + " | ".join(
                    [
                        title,
                        _escape(row["author"]),
                        _escape(row["data_status"]),
                        _display(row["followers"]),
                        _display(row["total_views"]),
                        _display(row["chapter_count"]),
                        _display(row["followers_per_1000_views"]),
                        _display(row["launch_index"]),
                    ]
                )
                + " |"
            )

    lines += ["", "## Current-batch medians", ""]
    for field, value in current["summary"]["medians"].items():
        lines.append(f"- `{field}`: **{_display(value)}**")

    lines += [
        "",
        f"## Rolling {rolling['lookback_hours']}h leaderboard",
        "",
    ]
    if rolling["novels"]:
        lines += [
            (
                "| # | Novel | Age h | Followers | Views | F/day | "
                "Views/day | Conversion | Index | RS |"
            ),
            "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for position, row in enumerate(
            rolling["novels"][:20],
            start=1,
        ):
            title = f"[{_escape(row['title'])}]({row['url']})"
            rs = (
                f"#{row['current_best_rs_rank']}"
                if row["current_best_rs_rank"]
                else "—"
            )
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(position),
                        title,
                        _display(row["age_hours"]),
                        _display(row["followers"]),
                        _display(row["total_views"]),
                        _display(row["followers_per_day"]),
                        _display(row["views_per_day"]),
                        _display(row["followers_per_1000_views"]),
                        _display(row["launch_index"]),
                        rs,
                    ]
                )
                + " |"
            )
    else:
        lines.append(
            "No prospectively observed launches are available in this window."
        )

    lines += ["", "## Leaders by dimension", ""]
    for label, leader in rolling["summary"]["leaders"].items():
        if leader:
            lines.append(
                f"- **{label}**: {leader['title']} — "
                f"`{leader['metric']}={_display(leader['value'])}`"
            )
        else:
            lines.append(f"- **{label}**: insufficient data")

    exceptions = rolling["summary"]["exceptions"]
    lines += ["", "## Data-quality exceptions", ""]
    if exceptions:
        for item in exceptions:
            lines.append(
                f"- `{item['fiction_id']}` **{item['title']}** — "
                f"{item['data_status']}; "
                f"availability={item['availability']}; "
                f"HTTP={item['http_status'] or '—'}; "
                f"next retry={item['next_detail_retry_utc'] or '—'}"
            )
    else:
        lines.append(
            "No unresolved data-quality exceptions in the rolling cohort."
        )

    lines += [
        "",
        "## Interpretation guardrails",
        "",
        (
            "- The launch index compares novels only inside the selected "
            "rolling cohort."
        ),
        (
            "- Rates use a six-hour age floor to limit unstable first-hour "
            "spikes."
        ),
        (
            "- Rising Stars status is reported as an outcome and is not "
            "included in the launch index."
        ),
        (
            "- Removed or unavailable works are classified separately rather "
            "than silently counted as missing data."
        ),
        "",
    ]
    return "\n".join(lines)


def _write_csv(
    path: Path,
    rows: Iterable[dict[str, Any]],
) -> None:
    fields = [
        "fiction_id",
        "title",
        "author",
        "url",
        "first_seen_utc",
        "published_utc",
        "age_hours",
        "data_status",
        "availability",
        "followers",
        "total_views",
        "chapter_count",
        "page_count",
        "rating_count",
        "rating_average",
        "followers_per_day",
        "views_per_day",
        "followers_per_1000_views",
        "followers_per_chapter",
        "views_per_chapter",
        "follower_growth",
        "view_growth",
        "follower_growth_per_day",
        "view_growth_per_day",
        "launch_index",
        "launch_index_confidence",
        "current_rs",
        "current_best_rs_rank",
        "first_rs_utc",
        "hours_to_rs",
        "latest_metric_observed_utc",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fields,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def write_launch_analysis(
    db_path: Path,
    report_dir: Path,
    run_id: int | None = None,
    *,
    current_launch_ids: Sequence[str] | None = None,
    lookback_hours: int = 168,
) -> dict[str, Any]:
    report = build_launch_analysis(
        db_path,
        run_id,
        current_launch_ids=current_launch_ids,
        lookback_hours=lookback_hours,
    )
    report_dir.mkdir(parents=True, exist_ok=True)
    run_id = int(report["run_id"])
    json_text = json.dumps(report, indent=2, ensure_ascii=False)
    markdown = render_launch_markdown(report)

    run_json = report_dir / f"launch_analysis_run_{run_id}.json"
    latest_json = report_dir / "launch_analysis_latest.json"
    run_md = report_dir / f"launch_analysis_run_{run_id}.md"
    latest_md = report_dir / "launch_analysis_latest.md"
    run_csv = report_dir / f"launch_analysis_run_{run_id}.csv"
    latest_csv = report_dir / "launch_analysis_latest.csv"

    run_json.write_text(json_text + "\n", encoding="utf-8")
    latest_json.write_text(json_text + "\n", encoding="utf-8")
    run_md.write_text(markdown, encoding="utf-8")
    latest_md.write_text(markdown, encoding="utf-8")
    _write_csv(run_csv, report["rolling_cohort"]["novels"])
    _write_csv(latest_csv, report["rolling_cohort"]["novels"])

    return {
        "report": report,
        "paths": {
            "json": str(run_json),
            "markdown": str(run_md),
            "csv": str(run_csv),
            "latest_json": str(latest_json),
            "latest_markdown": str(latest_md),
            "latest_csv": str(latest_csv),
        },
    }
