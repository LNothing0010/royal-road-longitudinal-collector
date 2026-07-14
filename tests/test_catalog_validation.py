from datetime import datetime, timezone
from pathlib import Path

from rrlab.config import CATALOG_BACKFILL_SOURCE
from rrlab.models import FictionObservation, SourceSnapshot
from rrlab.storage import Storage
from rrlab.validation import validate_run


def test_catalog_only_run_is_valid_without_rising_stars(tmp_path: Path):
    now = datetime(2026, 7, 14, tzinfo=timezone.utc)
    storage = Storage(tmp_path / "rrlab.sqlite", tmp_path / "raw")
    run_id = storage.begin_run(now, "test")
    snapshot = SourceSnapshot(
        run_timestamp_utc=now,
        source_name=CATALOG_BACKFILL_SOURCE.name,
        source_family=CATALOG_BACKFILL_SOURCE.family,
        source_url=CATALOG_BACKFILL_SOURCE.url,
        observed_count=1,
        complete=False,
        observations=[
            FictionObservation(
                observed_utc=now,
                source_name=CATALOG_BACKFILL_SOURCE.name,
                source_family=CATALOG_BACKFILL_SOURCE.family,
                rank=21,
                fiction_id="500",
                title="Historical Fiction",
                url="https://www.royalroad.com/fiction/500/historical-fiction",
            )
        ],
    )
    storage.persist_source(run_id, snapshot)
    storage.finish_run(run_id, "complete")

    report_path = validate_run(storage.db_path, run_id, tmp_path / "reports")
    report = report_path.read_text(encoding="utf-8")

    assert '"scope": "catalog"' in report
    assert '"valid_for_catalog_registry": true' in report
    assert '"valid_for_complete_rs_analysis": false' in report
    assert "missing_rs_source" not in report
