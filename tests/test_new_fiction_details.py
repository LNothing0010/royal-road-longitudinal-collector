from datetime import datetime, timezone
from pathlib import Path

from rrlab.collector import (
    _build_detail_plan,
    _missing_new_fiction_metrics,
    _ordered_required_detail_candidates,
)
from rrlab.storage import Storage


def _insert_fictions(storage: Storage, fiction_ids: list[str]) -> int:
    run_id = storage.begin_run(datetime(2026, 7, 16, tzinfo=timezone.utc), "test")
    with storage.connect() as conn:
        conn.executemany(
            """
            INSERT INTO fiction(
                fiction_id, title, url, author, first_seen_utc, last_seen_utc, first_seen_source
            ) VALUES (?, ?, ?, NULL, ?, ?, 'newest')
            """,
            [
                (
                    fiction_id,
                    f"Fiction {fiction_id}",
                    f"https://www.royalroad.com/fiction/{fiction_id}/test",
                    "2026-07-16T00:00:00Z",
                    "2026-07-16T00:00:00Z",
                )
                for fiction_id in fiction_ids
            ],
        )
        conn.commit()
    return run_id


def test_required_new_fictions_are_all_planned_before_regular_refreshes(tmp_path: Path):
    storage = Storage(tmp_path / "test.sqlite", tmp_path / "raw")
    _insert_fictions(storage, ["103", "101", "102"])

    required = _ordered_required_detail_candidates(
        storage,
        ["103", "101", "102"],
        backlog_limit=0,
    )
    regular = [
        {"fiction_id": "rs-1", "url": "https://example.com/rs-1"},
        {"fiction_id": "101", "url": "https://example.com/duplicate"},
        {"fiction_id": "rs-2", "url": "https://example.com/rs-2"},
    ]

    plan = _build_detail_plan(required, regular, detail_limit=4)

    assert [candidate["fiction_id"] for candidate in plan] == [
        "103",
        "101",
        "102",
        "rs-1",
    ]
    assert all(candidate["required_initial_detail"] for candidate in plan[:3])
    assert plan[3]["required_initial_detail"] is False


def test_required_new_fictions_override_a_smaller_detail_limit(tmp_path: Path):
    storage = Storage(tmp_path / "test.sqlite", tmp_path / "raw")
    _insert_fictions(storage, ["201", "202", "203"])

    required = _ordered_required_detail_candidates(
        storage,
        ["201", "202", "203"],
        backlog_limit=0,
    )
    plan = _build_detail_plan(required, [], detail_limit=1)

    assert [candidate["fiction_id"] for candidate in plan] == ["201", "202", "203"]


def test_missing_launch_metrics_require_followers_views_and_chapters(tmp_path: Path):
    storage = Storage(tmp_path / "test.sqlite", tmp_path / "raw")
    run_id = _insert_fictions(storage, ["301", "302", "303"])

    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO metric_observation(
                run_id, source_name, fiction_id, followers, total_views, chapter_count
            ) VALUES (?, 'fiction_detail', '301', 4, 120, 2)
            """,
            (run_id,),
        )
        conn.execute(
            """
            INSERT INTO metric_observation(
                run_id, source_name, fiction_id, followers, total_views, chapter_count
            ) VALUES (?, 'fiction_detail', '302', 3, NULL, 1)
            """,
            (run_id,),
        )
        conn.commit()

    assert _missing_new_fiction_metrics(
        storage,
        run_id,
        ["301", "302", "303"],
    ) == ["302", "303"]


def test_missing_new_fiction_backlog_is_scheduled_after_current_launches(tmp_path: Path):
    storage = Storage(tmp_path / "test.sqlite", tmp_path / "raw")
    _insert_fictions(storage, ["401", "402", "403"])

    required = _ordered_required_detail_candidates(
        storage,
        ["403"],
        backlog_limit=1,
    )

    assert [candidate["fiction_id"] for candidate in required] == ["403", "401"]
    assert required[0]["current_launch"] is True
    assert required[1]["current_launch"] is False
