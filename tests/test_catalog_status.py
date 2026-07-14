from pathlib import Path

from rrlab.catalog import catalog_status
from rrlab.config import Settings


def test_catalog_status_on_empty_registry(tmp_path: Path):
    settings = Settings(
        db_path=tmp_path / "rrlab.sqlite",
        raw_dir=tmp_path / "raw",
        report_dir=tmp_path / "reports",
        catalog_state_path=tmp_path / "catalog_state.json",
    )

    status = catalog_status(settings)

    assert status["total_registered_fictions"] == 0
    assert status["historical_catalog_fictions"] == 0
    assert status["prospectively_observed_newest_fictions"] == 0
    assert status["backfill_next_page"] == 2
    assert status["catalog_complete_once"] is False
