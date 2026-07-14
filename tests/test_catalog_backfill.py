from pathlib import Path

from rrlab.catalog import _read_state


def test_new_catalog_state_starts_after_frontier_page(tmp_path: Path):
    state = _read_state(tmp_path / "missing.json")

    assert state["version"] == 1
    assert state["backfill_next_page"] == 2
    assert state["backfill_pass"] == 1
