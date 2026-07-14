from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def validate_run(db_path: Path, run_id: int, report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    issues: list[dict] = []
    try:
        run = conn.execute("SELECT * FROM run WHERE run_id=?", (run_id,)).fetchone()
        sources = conn.execute("SELECT * FROM source_snapshot WHERE run_id=? ORDER BY source_name", (run_id,)).fetchall()
        for source in sources:
            if source["expected_count"] is not None and source["observed_count"] != source["expected_count"]:
                issues.append({"severity": "error", "code": "incomplete_source", "source": source["source_name"], "expected": source["expected_count"], "observed": source["observed_count"]})
            dup_rank = conn.execute(
                "SELECT rank,COUNT(*) c FROM listing_membership WHERE run_id=? AND source_name=? GROUP BY rank HAVING c>1",
                (run_id, source["source_name"]),
            ).fetchall()
            if dup_rank:
                issues.append({"severity": "error", "code": "duplicate_rank", "source": source["source_name"], "rows": [dict(r) for r in dup_rank]})
        # Public counters should normally be monotonic. Flag, do not overwrite.
        decreases = conn.execute(
            """
            WITH current AS (
              SELECT fiction_id,MAX(followers) followers,MAX(total_views) total_views
              FROM metric_observation WHERE run_id=? GROUP BY fiction_id
            ), prior AS (
              SELECT mo.fiction_id,MAX(mo.followers) followers,MAX(mo.total_views) total_views
              FROM metric_observation mo
              WHERE mo.run_id=(SELECT MAX(run_id) FROM run WHERE run_id<? AND status IN ('complete','partial'))
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
            "valid_for_complete_rs_analysis": not any(i["severity"] == "error" for i in issues),
        }
        path = report_dir / f"validation_run_{run_id}.json"
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        (report_dir / "validation_latest.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        return path
    finally:
        conn.close()
