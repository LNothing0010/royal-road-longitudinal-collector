from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def latest_complete_panel_timestamp(db_path: Path) -> str | None:
    if not db_path.exists():
        return None

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT r.timestamp_utc
            FROM run AS r
            JOIN source_snapshot AS s ON s.run_id = r.run_id
            WHERE r.status = 'complete'
              AND s.source_family = 'rising_stars'
              AND s.complete = 1
            GROUP BY r.run_id, r.timestamp_utc
            HAVING COUNT(DISTINCT s.source_name) >= 6
            ORDER BY r.timestamp_utc DESC
            LIMIT 1
            """
        ).fetchone()
    return None if row is None else str(row[0])


def cadence_decision(
    db_path: Path,
    *,
    min_age_minutes: float = 55.0,
    force: bool = False,
    now: datetime | None = None,
    trigger: str | None = None,
) -> dict[str, Any]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    last_panel_utc: str | None = None
    database_error: str | None = None

    try:
        last_panel_utc = latest_complete_panel_timestamp(db_path)
    except sqlite3.Error as exc:
        database_error = f"{type(exc).__name__}: {exc}"

    age_minutes: float | None = None
    if last_panel_utc is not None:
        age_minutes = max(
            0.0,
            (current - _parse_utc(last_panel_utc)).total_seconds() / 60.0,
        )

    if force:
        should_collect = True
        reason = "manual_dispatch"
    elif database_error is not None:
        should_collect = True
        reason = "database_unreadable"
    elif last_panel_utc is None:
        should_collect = True
        reason = "no_complete_panel"
    elif age_minutes is not None and age_minutes >= min_age_minutes:
        should_collect = True
        reason = "panel_due"
    else:
        should_collect = False
        reason = "panel_fresh"

    return {
        "generated_utc": current.isoformat(),
        "should_collect": should_collect,
        "reason": reason,
        "minimum_age_minutes": min_age_minutes,
        "last_complete_panel_utc": last_panel_utc,
        "age_minutes": None if age_minutes is None else round(age_minutes, 3),
        "force": force,
        "trigger": trigger,
        "database_error": database_error,
    }


def _write_github_outputs(payload: dict[str, Any]) -> None:
    output_path = os.getenv("GITHUB_OUTPUT")
    if not output_path:
        return

    values = {
        "should_collect": str(payload["should_collect"]).lower(),
        "reason": payload["reason"],
        "last_complete_panel_utc": payload["last_complete_panel_utc"] or "",
        "age_minutes": "" if payload["age_minutes"] is None else payload["age_minutes"],
    }
    with Path(output_path).open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def main() -> None:
    payload = cadence_decision(
        Path(os.getenv("RR_DB_PATH", "data/rrlab.sqlite")),
        min_age_minutes=float(os.getenv("RR_CADENCE_MIN_AGE_MINUTES", "55")),
        force=os.getenv("RR_CADENCE_FORCE", "0") == "1",
        trigger=os.getenv("RR_CADENCE_TRIGGER") or None,
    )
    report_path = Path(
        os.getenv("RR_CADENCE_REPORT", "reports/cadence_gate_latest.json")
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    _write_github_outputs(payload)
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
