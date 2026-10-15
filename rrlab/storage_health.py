from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Settings


def _tree_stats(root: Path) -> dict[str, int]:
    if not root.exists():
        return {"files": 0, "bytes": 0}
    files = [path for path in root.rglob("*") if path.is_file()]
    return {
        "files": len(files),
        "bytes": sum(path.stat().st_size for path in files),
    }


def build_storage_health(
    db_path: Path,
    raw_dir: Path,
    report_dir: Path,
    export_dir: Path | None = None,
) -> dict[str, Any]:
    export_dir = export_dir or db_path.parent / "export"
    db_bytes = db_path.stat().st_size if db_path.exists() else 0
    raw = _tree_stats(raw_dir)
    reports = _tree_stats(report_dir)
    exports = _tree_stats(export_dir)
    working_tree_bytes = db_bytes + raw["bytes"] + reports["bytes"] + exports["bytes"]
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "database": {"path": str(db_path), "bytes": db_bytes},
        "raw_snapshots": {"path": str(raw_dir), **raw},
        "reports": {"path": str(report_dir), **reports},
        "exports": {"path": str(export_dir), **exports},
        "working_tree_bytes": working_tree_bytes,
        "working_tree_megabytes": round(working_tree_bytes / (1024 * 1024), 3),
        "note": (
            "Working-tree size excludes Git object history. Repository size must be read "
            "from GitHub metadata or a local git count-objects audit."
        ),
    }


def write_storage_health(
    db_path: Path,
    raw_dir: Path,
    report_dir: Path,
    export_dir: Path | None = None,
) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = build_storage_health(db_path, raw_dir, report_dir, export_dir)
    path = report_dir / "storage_health_latest.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> None:
    settings = Settings()
    path = write_storage_health(settings.db_path, settings.raw_dir, settings.report_dir)
    print(path.read_text(encoding="utf-8"), end="")


if __name__ == "__main__":
    main()
