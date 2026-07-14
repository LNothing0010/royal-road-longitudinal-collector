import json
from pathlib import Path

import pytest

from rrlab.catalog import _read_state


def test_unsupported_catalog_state_version_fails_closed(tmp_path: Path):
    path = tmp_path / "catalog_state.json"
    path.write_text(json.dumps({"version": 999}), encoding="utf-8")

    with pytest.raises(RuntimeError, match="Unsupported catalog state version"):
        _read_state(path)
