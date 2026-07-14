from pathlib import Path

from rrlab.catalog import _read_state, _write_state


def test_catalog_state_roundtrip(tmp_path: Path):
    path = tmp_path / "catalog_state.json"
    _write_state(path, {"backfill_next_page": 77, "frontier_anchor_ids": ["1", "2"]})

    state = _read_state(path)

    assert state["version"] == 1
    assert state["backfill_next_page"] == 77
    assert state["frontier_anchor_ids"] == ["1", "2"]
    assert state["updated_utc"].endswith("Z")
    assert not path.with_suffix(".json.tmp").exists()
