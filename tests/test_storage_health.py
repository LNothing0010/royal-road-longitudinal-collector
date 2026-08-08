from pathlib import Path

from rrlab.storage_health import build_storage_health, write_storage_health


def test_storage_health_counts_files_and_bytes(tmp_path: Path):
    data = tmp_path / "data"
    raw = data / "raw"
    reports = tmp_path / "reports"
    exports = data / "export"
    raw.mkdir(parents=True)
    reports.mkdir()
    exports.mkdir(parents=True)
    db = data / "rrlab.sqlite"
    db.write_bytes(b"db")
    (raw / "a.json.gz").write_bytes(b"raw")
    (reports / "a.json").write_bytes(b"report")
    (exports / "a.zip").write_bytes(b"zip")

    payload = build_storage_health(db, raw, reports, exports)

    assert payload["database"]["bytes"] == 2
    assert payload["raw_snapshots"] == {"path": str(raw), "files": 1, "bytes": 3}
    assert payload["reports"] == {"path": str(reports), "files": 1, "bytes": 6}
    assert payload["exports"] == {"path": str(exports), "files": 1, "bytes": 3}
    assert payload["working_tree_bytes"] == 14


def test_write_storage_health_creates_latest_report(tmp_path: Path):
    data = tmp_path / "data"
    reports = tmp_path / "reports"
    path = write_storage_health(data / "rrlab.sqlite", data / "raw", reports)

    assert path == reports / "storage_health_latest.json"
    assert path.exists()
    assert '"working_tree_bytes": 0' in path.read_text(encoding="utf-8")
