from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .storage import Storage

DETAIL_FETCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS detail_fetch_state (
  fiction_id TEXT PRIMARY KEY,
  availability TEXT NOT NULL,
  http_status INTEGER,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  first_failure_utc TEXT,
  last_attempt_utc TEXT NOT NULL,
  next_retry_utc TEXT,
  last_error TEXT,
  FOREIGN KEY(fiction_id) REFERENCES fiction(fiction_id)
);
CREATE INDEX IF NOT EXISTS idx_detail_fetch_retry
ON detail_fetch_state(availability, next_retry_utc);
"""


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def ensure_detail_fetch_state(storage: Storage) -> None:
    with storage.connect() as conn:
        conn.executescript(DETAIL_FETCH_SCHEMA)
        conn.commit()


def http_status_from_exception(exc: Exception) -> int | None:
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        return int(exc.response.status_code)
    return None


def record_detail_success(storage: Storage, fiction_id: str, observed_utc: datetime) -> None:
    ensure_detail_fetch_state(storage)
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO detail_fetch_state(
              fiction_id,availability,http_status,consecutive_failures,
              first_failure_utc,last_attempt_utc,next_retry_utc,last_error
            ) VALUES(?, 'available', 200, 0, NULL, ?, NULL, NULL)
            ON CONFLICT(fiction_id) DO UPDATE SET
              availability='available',
              http_status=200,
              consecutive_failures=0,
              last_attempt_utc=excluded.last_attempt_utc,
              next_retry_utc=NULL,
              last_error=NULL
            """,
            (str(fiction_id), _utc_text(observed_utc)),
        )
        conn.commit()


def record_detail_failure(
    storage: Storage,
    fiction_id: str,
    observed_utc: datetime,
    exc: Exception,
) -> dict[str, Any]:
    ensure_detail_fetch_state(storage)
    status_code = http_status_from_exception(exc)
    unavailable = status_code in {404, 410}
    availability = "unavailable" if unavailable else "transient_error"

    with storage.connect() as conn:
        previous = conn.execute(
            "SELECT consecutive_failures,first_failure_utc FROM detail_fetch_state WHERE fiction_id=?",
            (str(fiction_id),),
        ).fetchone()
        failures = int(previous[0]) + 1 if previous else 1
        first_failure_utc = previous[1] if previous else _utc_text(observed_utc)

        if unavailable:
            retry_after = timedelta(days=7)
        else:
            retry_after = timedelta(hours=min(24, 2 ** min(failures, 4)))
        next_retry_utc = observed_utc + retry_after

        conn.execute(
            """
            INSERT INTO detail_fetch_state(
              fiction_id,availability,http_status,consecutive_failures,
              first_failure_utc,last_attempt_utc,next_retry_utc,last_error
            ) VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(fiction_id) DO UPDATE SET
              availability=excluded.availability,
              http_status=excluded.http_status,
              consecutive_failures=excluded.consecutive_failures,
              first_failure_utc=COALESCE(detail_fetch_state.first_failure_utc, excluded.first_failure_utc),
              last_attempt_utc=excluded.last_attempt_utc,
              next_retry_utc=excluded.next_retry_utc,
              last_error=excluded.last_error
            """,
            (
                str(fiction_id),
                availability,
                status_code,
                failures,
                first_failure_utc,
                _utc_text(observed_utc),
                _utc_text(next_retry_utc),
                f"{type(exc).__name__}: {exc}"[:2000],
            ),
        )
        conn.commit()

    return {
        "fiction_id": str(fiction_id),
        "availability": availability,
        "http_status": status_code,
        "consecutive_failures": failures,
        "next_retry_utc": _utc_text(next_retry_utc),
    }


def detail_retry_suppressed_ids(
    storage: Storage,
    fiction_ids: list[str] | tuple[str, ...],
    now: datetime | None = None,
) -> set[str]:
    ensure_detail_fetch_state(storage)
    ordered = list(dict.fromkeys(str(value) for value in fiction_ids))
    if not ordered:
        return set()
    placeholders = ",".join("?" for _ in ordered)
    current = _utc_text(now or datetime.now(timezone.utc))
    with storage.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT fiction_id
            FROM detail_fetch_state
            WHERE fiction_id IN ({placeholders})
              AND availability='unavailable'
              AND (next_retry_utc IS NULL OR julianday(next_retry_utc)>julianday(?))
            """,
            (*ordered, current),
        ).fetchall()
    return {str(row[0]) for row in rows}
