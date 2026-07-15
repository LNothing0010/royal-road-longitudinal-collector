import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rrlab.cadence import cadence_decision


def _create_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE run (
              run_id INTEGER PRIMARY KEY,
              timestamp_utc TEXT NOT NULL,
              status TEXT NOT NULL
            );
            CREATE TABLE source_snapshot (
              run_id INTEGER NOT NULL,
              source_name TEXT NOT NULL,
              source_family TEXT NOT NULL,
              complete INTEGER
            );
            """
        )


def _insert_panel(path: Path, run_id: int, timestamp: datetime) -> None:
    sources = (
        "rs_main",
        "rs_fantasy",
        "rs_action",
        "rs_adventure",
        "rs_drama",
        "rs_psychological",
    )
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO run(run_id, timestamp_utc, status) VALUES (?, ?, 'complete')",
            (run_id, timestamp.isoformat()),
        )
        conn.executemany(
            """
            INSERT INTO source_snapshot(run_id, source_name, source_family, complete)
            VALUES (?, ?, 'rising_stars', 1)
            """,
            [(run_id, source) for source in sources],
        )


def test_missing_database_collects(tmp_path: Path):
    decision = cadence_decision(tmp_path / "missing.sqlite")

    assert decision["should_collect"] is True
    assert decision["reason"] == "no_complete_panel"


def test_recent_complete_panel_skips(tmp_path: Path):
    db = tmp_path / "rrlab.sqlite"
    _create_db(db)
    now = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    _insert_panel(db, 1, now - timedelta(minutes=30))

    decision = cadence_decision(db, now=now, min_age_minutes=55)

    assert decision["should_collect"] is False
    assert decision["reason"] == "panel_fresh"
    assert decision["age_minutes"] == 30.0


def test_stale_complete_panel_collects(tmp_path: Path):
    db = tmp_path / "rrlab.sqlite"
    _create_db(db)
    now = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    _insert_panel(db, 1, now - timedelta(minutes=80))

    decision = cadence_decision(db, now=now, min_age_minutes=55)

    assert decision["should_collect"] is True
    assert decision["reason"] == "panel_due"


def test_manual_dispatch_forces_collection(tmp_path: Path):
    db = tmp_path / "rrlab.sqlite"
    _create_db(db)
    now = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    _insert_panel(db, 1, now - timedelta(minutes=5))

    decision = cadence_decision(db, now=now, min_age_minutes=55, force=True)

    assert decision["should_collect"] is True
    assert decision["reason"] == "manual_dispatch"


def test_incomplete_rs_run_does_not_satisfy_gate(tmp_path: Path):
    db = tmp_path / "rrlab.sqlite"
    _create_db(db)
    now = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO run(run_id, timestamp_utc, status) VALUES (1, ?, 'complete')",
            ((now - timedelta(minutes=5)).isoformat(),),
        )
        conn.execute(
            """
            INSERT INTO source_snapshot(run_id, source_name, source_family, complete)
            VALUES (1, 'rs_main', 'rising_stars', 1)
            """
        )

    decision = cadence_decision(db, now=now, min_age_minutes=55)

    assert decision["should_collect"] is True
    assert decision["reason"] == "no_complete_panel"
