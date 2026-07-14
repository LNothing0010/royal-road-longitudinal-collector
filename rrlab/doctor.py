from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .config import RS_SOURCES, SOURCE_MAP, Settings


def _check_writable(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    fd, probe = tempfile.mkstemp(prefix=".rrlab-write-test-", dir=path)
    os.close(fd)
    Path(probe).unlink(missing_ok=True)


def run_doctor(settings: Settings | None = None) -> dict:
    settings = settings or Settings()
    expected_rs = {
        "rs_main",
        "rs_fantasy",
        "rs_action",
        "rs_adventure",
        "rs_drama",
        "rs_psychological",
    }
    if set(RS_SOURCES) != expected_rs:
        raise RuntimeError(f"RS source contract mismatch: {sorted(RS_SOURCES)}")
    for name in RS_SOURCES:
        if SOURCE_MAP[name].expected_count != 50:
            raise RuntimeError(f"{name} must expect 50 rows")
    if len(SOURCE_MAP) != len(set(SOURCE_MAP)):
        raise RuntimeError("duplicate source names")
    if not settings.user_agent:
        raise RuntimeError("RR_USER_AGENT resolved to an empty value")

    _check_writable(settings.db_path.parent)
    _check_writable(settings.raw_dir)
    _check_writable(settings.report_dir)

    result = {
        "ok": True,
        "source_count": len(SOURCE_MAP),
        "rs_sources": list(RS_SOURCES),
        "db_path": str(settings.db_path),
        "raw_dir": str(settings.raw_dir),
        "report_dir": str(settings.report_dir),
        "min_delay_seconds": settings.min_delay_seconds,
        "timeout_seconds": settings.timeout_seconds,
        "user_agent_configured": bool(settings.user_agent),
    }
    return result


def print_doctor(settings: Settings | None = None) -> None:
    print(json.dumps(run_doctor(settings), indent=2, ensure_ascii=False))
