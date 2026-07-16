import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from rrlab.availability import record_detail_failure
from rrlab.launch_analysis import build_launch_analysis, write_launch_analysis
from rrlab.storage import Storage


def _utc(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _seed_panel(storage: Storage) -> tuple[int, int]:
    start = datetime(2026, 7, 16, 6, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=12)
    run_1 = storage.begin_run(start, "test")
    storage.finish_run(run_1, "complete")
    run_2 = storage.begin_run(end, "test")

    with storage.connect() as conn:
        conn.executemany(
            """
            INSERT INTO fiction(
              fiction_id,title,url,author,first_seen_utc,last_seen_utc,first_seen_source
            ) VALUES(?,?,?,?,?,?,?)
            """,
            [
                (
                    "1",
                    "Alpha Launch",
                    "https://example.com/1",
                    "A",
                    _utc(start),
                    _utc(end),
                    "rs_main",
                ),
                (
                    "2",
                    "Beta Launch",
                    "https://example.com/2",
                    "B",
                    _utc(start),
                    _utc(end),
                    "newest",
                ),
            ],
        )
        conn.executemany(
            """
            INSERT INTO source_snapshot(
              run_id,source_name,source_family,source_url,expected_count,observed_count,
              complete,warnings_json
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            [
                (
                    run_1,
                    "newest",
                    "discovery",
                    "https://example.com/new",
                    None,
                    2,
                    1,
                    "[]",
                ),
                (
                    run_2,
                    "newest",
                    "discovery",
                    "https://example.com/new",
                    None,
                    2,
                    1,
                    "[]",
                ),
                (
                    run_2,
                    "rs_main",
                    "rising_stars",
                    "https://example.com/rs",
                    50,
                    1,
                    0,
                    "[]",
                ),
            ],
        )
        conn.executemany(
            """
            INSERT INTO listing_membership(run_id,source_name,fiction_id,rank)
            VALUES(?,?,?,?)
            """,
            [
                (run_1, "newest", "1", 1),
                (run_1, "newest", "2", 2),
                (run_2, "newest", "1", 15),
                (run_2, "newest", "2", 16),
                (run_2, "rs_main", "2", 9),
            ],
        )
        conn.executemany(
            """
            INSERT INTO metric_observation(
              run_id,source_name,fiction_id,followers,total_views,chapter_count,page_count,
              rating_count,rating_average,first_chapter_utc
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    run_1,
                    "fiction_detail",
                    "1",
                    10,
                    100,
                    2,
                    20,
                    2,
                    4.0,
                    _utc(start - timedelta(hours=6)),
                ),
                (
                    run_1,
                    "fiction_detail",
                    "2",
                    20,
                    200,
                    4,
                    40,
                    4,
                    4.5,
                    _utc(start - timedelta(hours=12)),
                ),
                (
                    run_2,
                    "fiction_detail",
                    "1",
                    22,
                    340,
                    3,
                    30,
                    5,
                    4.1,
                    _utc(start - timedelta(hours=6)),
                ),
                (
                    run_2,
                    "fiction_detail",
                    "2",
                    44,
                    680,
                    5,
                    50,
                    10,
                    4.6,
                    _utc(start - timedelta(hours=12)),
                ),
            ],
        )
        conn.executemany(
            """
            INSERT INTO release_event(
              fiction_id,chapter_key,chapter_title,published_utc,first_observed_utc,
              last_observed_utc,source_name,date_precision
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            [
                (
                    "1",
                    "a",
                    "Chapter 1",
                    _utc(start - timedelta(hours=6)),
                    _utc(start),
                    _utc(end),
                    "fiction_detail",
                    "absolute",
                ),
                (
                    "2",
                    "b",
                    "Chapter 1",
                    _utc(start - timedelta(hours=12)),
                    _utc(start),
                    _utc(end),
                    "fiction_detail",
                    "absolute",
                ),
            ],
        )
        conn.commit()
    storage.finish_run(run_2, "complete")
    return run_1, run_2


def test_launch_report_is_human_readable_and_analytical(tmp_path: Path):
    storage = Storage(tmp_path / "rrlab.sqlite", tmp_path / "raw")
    _, run_id = _seed_panel(storage)

    output = write_launch_analysis(
        storage.db_path,
        tmp_path / "reports",
        run_id,
        current_launch_ids=["1", "2"],
        lookback_hours=48,
    )
    report = output["report"]

    assert report["current_batch"]["summary"]["novel_count"] == 2
    assert report["current_batch"]["summary"]["medians"]["followers"] == 33.0
    assert (
        report["rolling_cohort"]["summary"]["leaders"]["followers"]["title"]
        == "Beta Launch"
    )
    assert report["rolling_cohort"]["novels"][0]["launch_index"] is not None
    assert report["rolling_cohort"]["novels"][0]["discovery_source"] in {
        "newest",
        "rs_main",
    }
    assert Path(output["paths"]["latest_json"]).exists()
    assert Path(output["paths"]["latest_markdown"]).exists()
    assert Path(output["paths"]["latest_csv"]).exists()
    assert "Rolling 48h leaderboard" in Path(
        output["paths"]["latest_markdown"]
    ).read_text()


def test_removed_fiction_is_resolved_not_silently_missing(tmp_path: Path):
    storage = Storage(tmp_path / "rrlab.sqlite", tmp_path / "raw")
    now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    run_id = storage.begin_run(now, "test")
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO fiction(
              fiction_id,title,url,author,first_seen_utc,last_seen_utc,first_seen_source
            ) VALUES('404','Removed Novel','https://example.com/404',NULL,?,?, 'newest')
            """,
            (_utc(now), _utc(now)),
        )
        conn.execute(
            """
            INSERT INTO source_snapshot(
              run_id,source_name,source_family,source_url,observed_count,complete,warnings_json
            ) VALUES(?, 'newest', 'discovery', 'https://example.com/new', 1, 1, '[]')
            """,
            (run_id,),
        )
        conn.execute(
            """
            INSERT INTO listing_membership(run_id,source_name,fiction_id,rank)
            VALUES(?, 'newest', '404', 1)
            """,
            (run_id,),
        )
        conn.commit()

    request = httpx.Request("GET", "https://example.com/404")
    response = httpx.Response(404, request=request)
    error = httpx.HTTPStatusError(
        "not found",
        request=request,
        response=response,
    )
    record_detail_failure(storage, "404", now, error)
    storage.finish_run(run_id, "complete")

    report = build_launch_analysis(
        storage.db_path,
        run_id,
        current_launch_ids=["404"],
        lookback_hours=24,
    )
    row = report["current_batch"]["novels"][0]
    quality = report["current_batch"]["summary"]["data_quality"]

    assert row["data_status"] == "unavailable"
    assert row["http_status"] == 404
    assert quality["core_coverage_pct"] == 0.0
    assert quality["resolved_coverage_pct"] == 100.0
    assert (
        report["current_batch"]["summary"]["exceptions"][0]["title"]
        == "Removed Novel"
    )


def test_json_report_contains_methodology_and_exact_titles(tmp_path: Path):
    storage = Storage(tmp_path / "rrlab.sqlite", tmp_path / "raw")
    _, run_id = _seed_panel(storage)
    output = write_launch_analysis(
        storage.db_path,
        tmp_path / "reports",
        run_id,
        current_launch_ids=["1"],
    )

    payload = json.loads(Path(output["paths"]["json"]).read_text())
    assert payload["methodology"]["launch_index"]["minimum_components"] == 4
    assert payload["current_batch"]["novels"][0]["title"] == "Alpha Launch"
