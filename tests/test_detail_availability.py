from datetime import datetime, timezone
from pathlib import Path

import httpx

from rrlab.availability import record_detail_failure, record_detail_success
from rrlab.collector import _ordered_required_detail_candidates
from rrlab.storage import Storage


def _seed(storage: Storage) -> int:
    now = datetime(2026, 7, 16, tzinfo=timezone.utc)
    run_id = storage.begin_run(now, "test")
    with storage.connect() as conn:
        conn.executemany(
            """
            INSERT INTO fiction(
              fiction_id,title,url,author,first_seen_utc,last_seen_utc,first_seen_source
            ) VALUES(?,?,?,NULL,?,?,?)
            """,
            [
                (
                    "1",
                    "Available",
                    "https://example.com/1",
                    "2026-07-16T00:00:00Z",
                    "2026-07-16T00:00:00Z",
                    "newest",
                ),
                (
                    "2",
                    "Removed",
                    "https://example.com/2",
                    "2026-07-16T00:01:00Z",
                    "2026-07-16T00:01:00Z",
                    "rs_main",
                ),
            ],
        )
        conn.execute(
            """
            INSERT INTO source_snapshot(
              run_id,source_name,source_family,source_url,observed_count,complete,warnings_json
            ) VALUES(?, 'newest', 'discovery', 'https://example.com/new', 2, 1, '[]')
            """,
            (run_id,),
        )
        conn.executemany(
            """
            INSERT INTO listing_membership(run_id,source_name,fiction_id,rank)
            VALUES(?, 'newest', ?, ?)
            """,
            [(run_id, "1", 1), (run_id, "2", 2)],
        )
        conn.commit()
    return run_id


def _not_found(url: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", url)
    response = httpx.Response(404, request=request)
    return httpx.HTTPStatusError("not found", request=request, response=response)


def test_recent_404_is_excluded_from_backlog_even_if_first_seen_elsewhere(
    tmp_path: Path,
):
    storage = Storage(tmp_path / "rrlab.sqlite", tmp_path / "raw")
    _seed(storage)
    now = datetime(2026, 7, 16, tzinfo=timezone.utc)
    record_detail_failure(storage, "2", now, _not_found("https://example.com/2"))

    candidates = _ordered_required_detail_candidates(storage, [], backlog_limit=10)

    assert [candidate["fiction_id"] for candidate in candidates] == ["1"]


def test_success_resets_unavailable_state(tmp_path: Path):
    storage = Storage(tmp_path / "rrlab.sqlite", tmp_path / "raw")
    _seed(storage)
    now = datetime(2026, 7, 16, tzinfo=timezone.utc)
    record_detail_failure(storage, "2", now, _not_found("https://example.com/2"))
    record_detail_success(storage, "2", now)

    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT availability,consecutive_failures,next_retry_utc
            FROM detail_fetch_state
            WHERE fiction_id='2'
            """
        ).fetchone()

    assert row["availability"] == "available"
    assert row["consecutive_failures"] == 0
    assert row["next_retry_utc"] is None
