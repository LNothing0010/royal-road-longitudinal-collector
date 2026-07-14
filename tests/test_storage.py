from datetime import datetime, timezone
from pathlib import Path

from rrlab.config import SOURCE_MAP
from rrlab.parsers import parse_listing_html
from rrlab.storage import Storage


def test_storage_roundtrip(tmp_path: Path):
    html = Path("tests/fixtures/rising_stars_sample.html").read_text()
    now = datetime(2026, 7, 14, tzinfo=timezone.utc)
    snapshot = parse_listing_html(html, SOURCE_MAP["rs_main"], now)
    storage = Storage(tmp_path / "test.sqlite", tmp_path / "raw")
    run_id = storage.begin_run(now, "test")
    storage.persist_source(run_id, snapshot)
    storage.finish_run(run_id, "partial")
    with storage.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM fiction").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM listing_membership").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM metric_observation").fetchone()[0] == 2
