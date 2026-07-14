from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import RS_SOURCES


def validate_run(db_path: Path, run_id: int, report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    issues: list[dict] = []
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            issues.append(
                {
                    "severity": "error",
                    "code": "sqlite_integrity",
                    "result": integrity,
                }
            )

        run = conn.execute("SELECT * FROM run WHERE run_id=?", (run_id,)).fetchone()
        sources = conn.execute(
            "SELECT * FROM source_snapshot WHERE run_id=? ORDER BY source_name",
            (run_id,),
        ).fetchall()
        source_map = {source["source_name"]: source for source in sources}

        if run is None:
            issues.append({"severity": "error", "code": "missing_run", "run_id": run_id})
        elif run["status"] == "failed":
            issues.append(
                {
                    "severity": "error",
                    "code": "failed_run",
                    "notes": run["notes"],
                }
            )

        for source_name in RS_SOURCES:
            source = source_map.get(source_name)
            if source is None:
                issues.append(
                    {
                        "severity": "error",
                        "code": "missing_rs_source",
                        "source": source_name,
                    }
                )
                continue
            expected = source["expected_count"]
            observed = source["observed_count"]
            if expected is None or observed != expected or source["complete"] != 1:
                issues.append(
                    {
                        "severity": "error",
                        "code": "incomplete_rs_source",
                        "source": source_name,
                        "expected": expected,
                        "observed": observed,
                        "complete": source["complete"],
                    }
                )
            membership_count = conn.execute(
                "SELECT COUNT(*) FROM listing_membership WHERE run_id=? AND source_name=?",
                (run_id, source_name),
            ).fetchone()[0]
            if expected is not None and membership_count != expected:
                issues.append(
                    {
                        "severity": "error",
                        "code": "rs_membership_count",
                        "source": source_name,
                        "expected": expected,
                        "observed": membership_count,
                    }
                )

        for source in sources:
            dup_rank = conn.execute(
                """
                SELECT rank,COUNT(*) c
                FROM listing_membership
                WHERE run_id=? AND source_name=?
                GROUP BY rank HAVING c>1
                """,
                (run_id, source["source_name"]),
            ).fetchall()
            if dup_rank:
                issues.append(
                    {
                        "severity": "error",
                        "code": "duplicate_rank",
                        "source": source["source_name"],
                        "rows": [dict(row) for row in dup_rank],
                    }
                )

        # Public counters should normally be monotonic. Flag, do not overwrite.
        decreases = conn.execute(
            """
            WITH current AS (
              SELECT fiction_id,MAX(followers) followers,MAX(total_views) total_views
              FROM metric_observation WHERE run_id=? GROUP BY fiction_id
            ), prior AS (
              SELECT mo.fiction_id,MAX(mo.followers) followers,MAX(mo.total_views) total_views
              FROM metric_observation mo
              WHERE mo.run_id=(
                SELECT MAX(run_id) FROM run
                WHERE run_id<? AND status IN ('complete','partial')
              )
              GROUP BY mo.fiction_id
            )
            SELECT c.fiction_id,c.followers current_followers,p.followers prior_followers,
                   c.total_views current_views,p.total_views prior_views
            FROM current c JOIN prior p USING(fiction_id)
            WHERE (c.followers IS NOT NULL AND p.followers IS NOT NULL AND c.followers<p.followers)
               OR (c.total_views IS NOT NULL AND p.total_views IS NOT NULL AND c.total_views<p.total_views)
            """,
            (run_id, run_id),
        ).fetchall()
        for row in decreases:
            issues.append({"severity": "warning", "code": "counter_decrease", **dict(row)})

        report = {
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "run": dict(run) if run else None,
            "sources": [dict(row) for row in sources],
            "issue_count": len(issues),
            "issues": issues,
            "valid_for_complete_rs_analysis": not any(
                issue["severity"] == "error" for issue in issues
            ),
        }
        path = report_dir / f"validation_run_{run_id}.json"
        payload = json.dumps(report, indent=2, ensure_ascii=False)
        path.write_text(payload, encoding="utf-8")
        (report_dir / "validation_latest.json").write_text(payload, encoding="utf-8")
        return path
    finally:
        conn.close()


def validate_latest(db_path: Path, report_dir: Path) -> Path:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT MAX(run_id) FROM run").fetchone()
    if row is None or row[0] is None:
        raise RuntimeError("No collection run is available for validation")
    return validate_run(db_path, int(row[0]), report_dir)
