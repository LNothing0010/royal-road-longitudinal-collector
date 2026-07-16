from datetime import datetime, timedelta, timezone
from pathlib import Path

from rrlab.longitudinal import (
    ANALYSIS_VERSION,
    _record_attempt,
    bootstrap_tracking,
    tracked_candidates,
    write_longitudinal_analysis,
)
from rrlab.models import DetailSnapshot, FictionObservation
from rrlab.storage import Storage


def _detail(
    observed: datetime,
    *,
    views: int,
    followers: int,
    chapters: int,
    favorites: int = 0,
    ratings: int = 0,
) -> DetailSnapshot:
    return DetailSnapshot(
        run_timestamp_utc=observed,
        observation=FictionObservation(
            observed_utc=observed,
            source_name="fiction_detail",
            source_family="detail",
            fiction_id="101",
            title="Tracked Story",
            author="Author",
            url="https://www.royalroad.com/fiction/101/tracked-story",
            followers=followers,
            total_views=views,
            average_views=float(views) / chapters,
            favorites=favorites,
            chapter_count=chapters,
            page_count=chapters * 10,
            rating_count=ratings,
            first_chapter_utc=observed - timedelta(hours=12),
            last_update_utc=observed,
        ),
    )


def test_repeated_detail_snapshots_remain_append_only(tmp_path: Path):
    storage = Storage(tmp_path / "rrlab.sqlite", tmp_path / "raw")
    start = datetime(2026, 7, 17, 6, 0, tzinfo=timezone.utc)
    run_1 = storage.begin_run(start, "test")
    storage.persist_detail(
        run_1,
        _detail(start, views=100, followers=1, chapters=1),
    )
    storage.finish_run(run_1, "complete")

    end = start + timedelta(hours=6)
    run_2 = storage.begin_run(end, "test")
    storage.persist_detail(
        run_2,
        _detail(
            end,
            views=160,
            followers=3,
            chapters=2,
            favorites=1,
            ratings=2,
        ),
    )
    storage.finish_run(run_2, "complete")

    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT run_id,total_views,followers,chapter_count
            FROM metric_observation
            WHERE fiction_id='101' AND source_name='fiction_detail'
            ORDER BY run_id
            """
        ).fetchall()

    assert [(row["run_id"], row["total_views"]) for row in rows] == [
        (run_1, 100),
        (run_2, 160),
    ]


def test_analysis_is_versioned_and_compares_immediate_previous_snapshot(tmp_path: Path):
    storage = Storage(tmp_path / "rrlab.sqlite", tmp_path / "raw")
    start = datetime(2026, 7, 17, 6, 0, tzinfo=timezone.utc)
    run_1 = storage.begin_run(start, "test")
    storage.persist_detail(
        run_1,
        _detail(start, views=100, followers=1, chapters=1),
    )
    storage.finish_run(run_1, "complete")

    end = start + timedelta(hours=6)
    run_2 = storage.begin_run(end, "test")
    storage.persist_detail(
        run_2,
        _detail(
            end,
            views=160,
            followers=3,
            chapters=2,
            favorites=1,
            ratings=2,
        ),
    )
    storage.finish_run(run_2, "complete")

    assert bootstrap_tracking(storage) == 1
    output = write_longitudinal_analysis(
        storage,
        run_2,
        tmp_path / "reports",
    )
    row = output["report"]["novels"][0]

    assert row["analysis_version"] == ANALYSIS_VERSION
    assert row["sampled_this_run"] is True
    assert row["previous_metric_run_id"] == run_1
    assert row["latest_metric_run_id"] == run_2
    assert row["elapsed_since_previous_hours"] == 6.0
    assert row["view_delta_since_previous"] == 60
    assert row["follower_delta_since_previous"] == 2
    assert row["chapter_delta_since_previous"] == 1
    assert row["views_per_day_increment"] == 240.0
    assert Path(output["paths"]["history_csv"]).exists()

    with storage.connect() as conn:
        persisted = conn.execute(
            """
            SELECT analysis_version,view_delta,follower_delta
            FROM analysis_observation
            WHERE run_id=? AND fiction_id='101'
            """,
            (run_2,),
        ).fetchone()

    assert persisted["analysis_version"] == ANALYSIS_VERSION
    assert persisted["view_delta"] == 60
    assert persisted["follower_delta"] == 2


def test_analysis_only_run_still_appends_a_versioned_derived_row(tmp_path: Path):
    storage = Storage(tmp_path / "rrlab.sqlite", tmp_path / "raw")
    start = datetime(2026, 7, 17, 6, 0, tzinfo=timezone.utc)
    run_1 = storage.begin_run(start, "test")
    storage.persist_detail(
        run_1,
        _detail(start, views=100, followers=1, chapters=1),
    )
    storage.finish_run(run_1, "complete")

    second = start + timedelta(hours=6)
    run_2 = storage.begin_run(second, "test")
    storage.persist_detail(
        run_2,
        _detail(second, views=160, followers=3, chapters=2),
    )
    storage.finish_run(run_2, "complete")
    bootstrap_tracking(storage)
    write_longitudinal_analysis(storage, run_2, tmp_path / "reports")

    third = start + timedelta(hours=12)
    run_3 = storage.begin_run(third, "test")
    output = write_longitudinal_analysis(storage, run_3, tmp_path / "reports")
    storage.finish_run(run_3, "complete")

    assert output["report"]["novels"][0]["sampled_this_run"] is False
    with storage.connect() as conn:
        count = conn.execute(
            """
            SELECT COUNT(*) FROM analysis_observation
            WHERE fiction_id='101' AND analysis_version=?
            """,
            (ANALYSIS_VERSION,),
        ).fetchone()[0]
    assert count == 2


def test_tracking_and_fetch_attempts_cover_known_fiction_across_runs(tmp_path: Path):
    storage = Storage(tmp_path / "rrlab.sqlite", tmp_path / "raw")
    now = datetime(2026, 7, 17, 6, 0, tzinfo=timezone.utc)
    run_1 = storage.begin_run(now, "test")
    storage.persist_detail(
        run_1,
        _detail(now, views=10, followers=0, chapters=1),
    )
    storage.finish_run(run_1, "complete")
    bootstrap_tracking(storage)

    candidates = tracked_candidates(storage, now=now + timedelta(hours=1))
    assert [candidate["fiction_id"] for candidate in candidates] == ["101"]

    run_2 = storage.begin_run(now + timedelta(hours=1), "test")
    _record_attempt(
        storage,
        run_id=run_2,
        fiction_id="101",
        attempted_utc=now + timedelta(hours=1),
        outcome="success",
        availability="available",
        http_status=200,
        raw_json_path="data/raw/detail.json.gz",
    )
    storage.finish_run(run_2, "complete")

    run_3 = storage.begin_run(now + timedelta(hours=2), "test")
    error = RuntimeError("temporary failure")
    _record_attempt(
        storage,
        run_id=run_3,
        fiction_id="101",
        attempted_utc=now + timedelta(hours=2),
        outcome="failure",
        availability="transient_error",
        http_status=None,
        error=error,
    )
    storage.finish_run(run_3, "partial")

    with storage.connect() as conn:
        attempts = conn.execute(
            """
            SELECT outcome FROM detail_fetch_observation
            WHERE fiction_id='101' ORDER BY run_id
            """
        ).fetchall()
        tracking = conn.execute(
            """
            SELECT request_count,success_count,failure_count
            FROM longitudinal_tracking WHERE fiction_id='101'
            """
        ).fetchone()

    assert [row["outcome"] for row in attempts] == ["success", "failure"]
    assert dict(tracking) == {
        "request_count": 2,
        "success_count": 1,
        "failure_count": 1,
    }
